# -*- coding: utf-8 -*-
#
# This file is part of WEKO3.
# Copyright (C) 2017 National Institute of Informatics.
#
# WEKO3 is free software; you can redistribute it
# and/or modify it under the terms of the GNU General Public License as
# published by the Free Software Foundation; either version 2 of the
# License, or (at your option) any later version.
#
# WEKO3 is distributed in the hope that it will be
# useful, but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with WEKO3; if not, write to the
# Free Software Foundation, Inc., 59 Temple Place, Suite 330, Boston,
# MA 02111-1307, USA.

"""Record API."""

from copy import deepcopy

from flask import current_app
from flask_babelex import gettext as _
from invenio_db import db
from invenio_pidstore.models import PersistentIdentifier
from invenio_records.api import Record
from invenio_records.errors import MissingModelError
from invenio_records.signals import after_record_delete, after_record_insert, \
    after_record_revert, after_record_update, before_record_delete, \
    before_record_insert, before_record_revert, before_record_update
from jsonpatch import apply_patch
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.sql.expression import desc
from werkzeug.local import LocalProxy

from .fetchers import weko_record_fetcher
from .models import FeedbackMailList as _FeedbackMailList
from .models import FileMetadata, ItemMetadata, ItemReference, ItemType
from .models import ItemTypeEditHistory as ItemTypeEditHistoryModel
from .models import ItemTypeMapping, ItemTypeName, ItemTypeProperty, \
    SiteLicenseInfo, SiteLicenseIpAddress

_records_state = LocalProxy(
    lambda: current_app.extensions['invenio-records'])


class RecordBase(dict):
    """Base class for Record and RecordBase."""

    def __init__(self, data, model=None):
        """Initialize instance with dictionary data and SQLAlchemy model.

        :param data: Dict with record metadata.
        :param model: :class:`~weko_records.models.MappingMetadata` instance.
        """
        self.model = model
        super(RecordBase, self).__init__(data or {})

    @property
    def id(self):
        """Get model identifier."""
        return self.model.id if self.model else None

    @property
    def revision_id(self):
        """Get revision identifier."""
        return self.model.version_id - 1 if self.model else None

    @property
    def created(self):
        """Get creation timestamp."""
        return self.model.created if self.model else None

    @property
    def updated(self):
        """Get last updated timestamp."""
        return self.model.updated if self.model else None

    def validate(self, **kwargs):
        r"""Validate record according to schema defined in ``$schema`` key.

        :Keyword Arguments:
          * **format_checker** --
            A ``format_checker`` is an instance of class
            :class:`jsonschema.FormatChecker` containing business logic to
            validate arbitrary formats. For example:

            >>> from jsonschema import FormatChecker
            >>> from jsonschema.validators import validate
            >>> checker = FormatChecker()
            >>> checker.checks('foo')(lambda el: el.startswith('foo'))
            <function <lambda> at ...>
            >>> validate('foo', {'format': 'foo'}, format_checker=checker)

            returns ``None``, which means that the validation was successful,
            while

            >>> validate('bar', {'format': 'foo'},
            ...    format_checker=checker)  # doctest: +IGNORE_EXCEPTION_DETAIL
            Traceback (most recent call last):
              ...
            ValidationError: 'bar' is not a 'foo'
              ...

            raises a :class:`jsonschema.exceptions.ValidationError`.

          * **validator** --
            A :class:`jsonschema.IValidator` class used for record validation.
            It will be used as `cls` argument when calling
            :func:`jsonschema.validate`. For example

            >>> from jsonschema.validators import extend, Draft4Validator
            >>> NoRequiredValidator = extend(
            ...     Draft4Validator,
            ...     validators={'required': lambda v, r, i, s: None}
            ... )
            >>> schema = {
            ...     'type': 'object',
            ...     'properties': {
            ...         'name': { 'type': 'string' },
            ...         'email': { 'type': 'string' },
            ...         'address': {'type': 'string' },
            ...         'telephone': { 'type': 'string' }
            ...     },
            ...     'required': ['name', 'email']
            ... }
            >>> from jsonschema.validators import validate
            >>> validate({}, schema, NoRequiredValidator)

            returns ``None``, which means that the validation was successful,
            while

            >>> validate({}, schema)  # doctest: +IGNORE_EXCEPTION_DETAIL
            Traceback (most recent call last):
            ...
            ValidationError: 'name' is a required property
            ...

            raises a :class:`jsonschema.exceptions.ValidationError`.
        """
        if '$schema' in self and self['$schema'] is not None:
            kwargs['cls'] = kwargs.pop('validator', None)
            _records_state.validate(self, self['$schema'], **kwargs)

    def replace_refs(self):
        """Replace the ``$ref`` keys within the JSON."""
        return _records_state.replace_refs(self)

    def dumps(self, **kwargs):
        """Return pure Python dictionary with record metadata."""
        return deepcopy(dict(self))


class ItemTypeNames(RecordBase):
    """Define API for ItemTypeName creation and manipulation."""

    @classmethod
    def update(cls, obj):
        """Update method."""
        def commit(olst, flg):
            with db.session.begin_nested():
                for lst in olst:
                    lst.has_site_license = flg
                    flag_modified(lst, 'has_site_license')
                    db.session.merge(lst)

        if isinstance(obj, dict):
            for k, v in obj.items():
                if v and isinstance(v, list):
                    ids = []
                    for lst in v:
                        id = lst.get('id')
                        if id:
                            ids.append(id)
                    olst = cls.get_all_by_id(ids)
                    commit(olst, True if 'allow' in k else False)

    @classmethod
    def get_all_by_id(cls, ids, with_deleted=False):
        """Retrieve item types by ids.

        :param ids: List of item type IDs.
        :returns: A list of :class:`ItemTypeName` instances.
        """
        with db.session.no_autoflush:
            query = ItemTypeName.query.filter(ItemTypeName.id.in_(ids))
            if not with_deleted:
                query = query.filter_by(is_active=True)
            return query.all()

    @classmethod
    def get_record(cls, id_, with_deleted=False):
        """Retrieve the item type name by id.

        :param id_: Identifier of item type name.
        :param with_deleted: If `True` then it includes deleted item type name.
        :returns: The :class:`ItemTypeName` instance.
        """
        with db.session.no_autoflush:
            query = ItemTypeName.query.filter_by(id=id_)
            if not with_deleted:
                query = query.filter_by(is_active=True)  # noqa
            return query.one_or_none()

    def delete(self, force=False):
        """Delete an item type name.

        If `force` is ``False``, the record is soft-deleted: record data will
        be deleted but the record identifier and the history of the record will
        be kept. This ensures that the same record identifier cannot be used
        twice, and that you can still retrieve its history. If `force` is
        ``True``, then the record is completely deleted from the database.

        #. Send a signal :data:`weko_records.signals.before_record_delete`
           with the current record as parameter.

        #. Delete or soft-delete the current record.

        #. Send a signal :data:`weko_records.signals.after_record_delete`
           with the current deleted record as parameter.

        :param force: if ``True``, deletes the current item type name
               from the database, otherwise soft-deletes it.
        :returns: The deleted :class:`ItemTypeName` instance.
        """
        with db.session.begin_nested():
            before_record_delete.send(
                current_app._get_current_object(),
                record=self
            )

            if force:
                db.session.delete(self)
            else:
                self.is_active = False
                db.session.merge(self)

        after_record_delete.send(
            current_app._get_current_object(),
            record=self
        )
        return self

    def restore(self):
        """Restore an logically deleted item type name.

        #. Restore the current record.

        :returns: The restored :class:`ItemTypeName` instance.
        """
        with db.session.begin_nested():
            self.is_active = True
            db.session.merge(self)

        return self


class ItemTypes(RecordBase):
    """Define API for item type creation and manipulation."""

    @classmethod
    def create(cls, item_type_name=None, name=None, schema=None, form=None,
               render=None, tag=1):
        r"""Create a new item type instance and store it in the database.

        #. Send a signal :data:`weko_records.signals.before_record_insert`
           with the new record as parameter.

        #. Validate the new record data.

        #. Add the new record in the database.

        #. Send a signal :data:`weko_records.signals.after_record_insert`
           with the new created record as parameter.

        :Keyword Arguments:
          * **format_checker** --
            An instance of the class :class:`jsonschema.FormatChecker`, which
            contains validation rules for formats. See
            :func:`~weko_records.api.RecordBase.validate` for more details.

          * **validator** --
            A :class:`jsonschema.IValidator` class that will be used to
            validate the record. See
            :func:`~weko_records.api.RecordBase.validate` for more details.

        :param item_type_name: Instance of class:`ItemTypeName`.
        :param name: Name of item type.
        :param schema: Schema in JSON format.
        :param form: Schema form in JSON format.
        :param render: Page render information in JSON format.
        :param tag: Tag of item type.
        :returns: A new :class:`ItemTypes` instance.
        """
        assert name
        with db.session.begin_nested():
            record = cls(schema)

            before_record_insert.send(
                current_app._get_current_object(),
                record=record,
            )
            if item_type_name is None:
                item_type_name = ItemTypeName(name=name)

            # record.validate(**kwargs)

            record.model = ItemType(item_type_name=item_type_name,
                                    schema=record, form=form, render=render,
                                    tag=tag)
            db.session.add(item_type_name)

        after_record_insert.send(
            current_app._get_current_object(),
            record=record,
        )
        return record

    @classmethod
    def update(cls, id_=0, name=None, schema=None, form=None, render=None,
               tag=1):
        r"""Update an item type instance and store it in the database.

        :param id_: Identifier of item type.
        :param name: Name of item type.
        :param schema: Schema in JSON format.
        :param form: Schema form in JSON format.
        :param render: Page render information in JSON format.
        :param tag: Tag of item type.
        :returns: The :class:`ItemTypes` instance.
        """
        assert name
        item_type_name = None
        # Create a new record
        if not id_ or id_ <= 0:
            return cls.create(item_type_name=item_type_name, name=name,
                              schema=schema, form=form, render=render, tag=tag)
        # Update for existed record
        else:
            with db.session.no_autoflush:
                # Get the item type by identifier
                result = cls.get_by_id(id_=id_)
                if result is None:
                    current_app.logger.debug('Invalid id: {}'.format(id_))
                    raise ValueError(_('Invalid id.'))

                # Get the latest tag of item type by name identifier
                result = cls.get_by_name_id(name_id=result.name_id)
                old_render = deepcopy(result[0].render)
                new_render = deepcopy(render)
                from weko_records.utils import check_to_upgrade_version
                upgrade_version = True if \
                    check_to_upgrade_version(old_render, new_render) else False
                updated_name = False
                tag = result[0].tag + 1
                # Check if the name has been changed
                item_type_name = result[0].item_type_name
                if name != item_type_name.name:
                    # Check if the new name has been existed
                    result = ItemTypeName.query.filter_by(
                        name=name).filter_by(is_active=True).one_or_none()
                    if result is not None:
                        current_app.logger.debug(
                            'Invalid name: {}'.format(name))
                        raise ValueError(_('Invalid name.'))
                    item_type_name.name = name
                    updated_name = True
                if upgrade_version or updated_name:
                    return cls.create(item_type_name=item_type_name, name=name,
                                      schema=schema, form=form, render=render,
                                      tag=tag)
                else:
                    current_record = cls.get_record(id_)
                    current_record.model.schema = schema
                    current_record.model.form = form
                    current_record.model.render = render
                    return current_record.commit()

    @classmethod
    def get_record(cls, id_, with_deleted=False):
        """Retrieve the item type by id.

        :param id_: Identifier of item type.
        :param with_deleted: If `True` then it includes deleted item types.
        :returns: The :class:`ItemTypes` instance.
        """
        with db.session.no_autoflush:
            query = ItemType.query.filter_by(id=id_)
            if not with_deleted:
                query = query.filter(ItemType.is_deleted.is_(False))  # noqa
            obj = query.one_or_none()
            if obj is None:
                return None
            return cls(obj.schema, model=obj)

    @classmethod
    def get_records(cls, ids, with_deleted=False):
        """Retrieve multiple item types by id.

        :param ids: List of item type IDs.
        :param with_deleted: If `True` then it includes deleted item types.
        :returns: A list of :class:`ItemTypes` instances.
        """
        with db.session.no_autoflush:
            query = ItemType.query.filter(ItemType.id.in_(ids))
            if not with_deleted:
                query = query.filter(ItemType.is_deleted.is_(False))  # noqa
            return [cls(obj.json, model=obj) for obj in query.all()]

    @classmethod
    def get_by_id(cls, id_, with_deleted=False):
        """Retrieve the item type by id.

        :param id_: Identifier of item type.
        :param with_deleted: If `True` then it includes deleted item types.
        :returns: The :class:`ItemTypes` instance.
        """
        with db.session.no_autoflush:
            query = ItemType.query.filter_by(id=id_)
            if not with_deleted:
                query = query.filter(ItemType.is_deleted.is_(False))  # noqa
            return query.one_or_none()

    @classmethod
    def get_by_name_id(cls, name_id, with_deleted=False):
        """Retrieve multiple item types by name identifier.

        :param name_id: Name identifier of item type.
        :param with_deleted: If `True` then it includes deleted item types.
        :returns: A list of :class:`ItemTypes` instance.
        """
        with db.session.no_autoflush:
            query = ItemType.query.filter_by(name_id=name_id)
            if not with_deleted:
                query = query.filter(ItemType.is_deleted.is_(False))  # noqa
            return query.order_by(desc(ItemType.tag)).all()

    @classmethod
    def get_records_by_name_id(cls, name_id, with_deleted=False):
        """Retrieve multiple item types by name identifier.

        :param name_id: Name identifier of item type.
        :param with_deleted: If `True` then it includes deleted item types.
        :returns: A list of :class:`ItemTypes` instance.
        """
        with db.session.no_autoflush:
            query = ItemType.query.filter_by(name_id=name_id)
            if not with_deleted:
                query = query.filter(ItemType.is_deleted.is_(False))  # noqa
            return [cls(obj.schema, model=obj) for obj in query.all()]

    @classmethod
    def get_latest(cls, with_deleted=False):
        """Retrieve the latest item types.

        :param with_deleted: If `True` then it includes deleted item types.
        :returns: A list of :class:`ItemTypes` instances.
        """
        with db.session.no_autoflush:
            query = ItemTypeName.query
            if not with_deleted:
                query = query.join(ItemType).filter(
                    ItemType.is_deleted.is_(False))
            return query.order_by(ItemTypeName.id).all()

    @classmethod
    def get_latest_custorm_harvesting(cls, with_deleted=False,
                                      harvesting_type=False):
        """Retrieve the latest item types.

        :param
        with_deleted: If `True` then it includes deleted item types.
        harvesting_type: If `True` then it includes multy item types.
        :returns: A list of :class:`ItemTypes` instances.
        """
        with db.session.no_autoflush:
            query = ItemTypeName.query
            if not with_deleted:
                query = query.join(ItemType).filter(
                    ItemType.is_deleted.is_(False),
                    ItemType.harvesting_type.is_(harvesting_type)
                )
            return query.order_by(ItemTypeName.id).all()

    @classmethod
    def get_all(cls, with_deleted=False):
        """Retrieve all item types.

        :param with_deleted: If `True` then it includes deleted item types.
        :returns: A list of :class:`ItemTypes` instances.
        """
        with db.session.no_autoflush:
            query = ItemType.query
            if not with_deleted:
                query = query.filter(ItemType.is_deleted.is_(False))  # noqa
            return query.order_by(ItemType.name_id, ItemType.tag).all()

    def patch(self, patch):
        """Patch item type metadata.

        :params patch: Dictionary of item type metadata.
        :returns: A new :class:`ItemTypes` instance.
        """
        data = apply_patch(dict(self), patch)
        return self.__class__(data, model=self.model)

    def commit(self, **kwargs):
        r"""Store changes of the current item type instance in the database.

        #. Send a signal :data:`weko_records.signals.before_record_update`
           with the current record to be committed as parameter.

        #. Validate the current record data.

        #. Commit the current record in the database.

        #. Send a signal :data:`weko_records.signals.after_record_update`
            with the committed record as parameter.

        :Keyword Arguments:
          * **format_checker** --
            An instance of the class :class:`jsonschema.FormatChecker`, which
            contains validation rules for formats. See
            :func:`~weko_records.api.RecordBase.validate` for more details.

          * **validator** --
            A :class:`jsonschema.IValidator` class that will be used to
            validate the record. See
            :func:`~weko_records.api.RecordBase.validate` for more details.

        :returns: The :class:`ItemTypes` instance.
        """
        if self.model is None:
            raise MissingModelError()

        with db.session.begin_nested():
            before_record_update.send(
                current_app._get_current_object(),
                record=self
            )

            # self.validate(**kwargs)

            # self.model.json = dict(self)
            # flag_modified(self.model, 'json')

            db.session.merge(self.model)

        after_record_update.send(
            current_app._get_current_object(),
            record=self
        )
        return self

    def delete(self, force=False):
        """Delete an item type.

        If `force` is ``False``, the record is soft-deleted: record data will
        be deleted but the record identifier and the history of the record will
        be kept. This ensures that the same record identifier cannot be used
        twice, and that you can still retrieve its history. If `force` is
        ``True``, then the record is completely deleted from the database.

        #. Send a signal :data:`weko_records.signals.before_record_delete`
           with the current record as parameter.

        #. Delete or soft-delete the current record.

        #. Send a signal :data:`weko_records.signals.after_record_delete`
           with the current deleted record as parameter.

        :param force: if ``True``, deletes the current item type from
               the database, otherwise soft-deletes it.
        :returns: The deleted :class:`ItemTypes` instance.
        """
        if self.model is None:
            raise MissingModelError()

        with db.session.begin_nested():
            before_record_delete.send(
                current_app._get_current_object(),
                record=self
            )

            if force:
                db.session.delete(self.model)
            else:
                self.model.is_deleted = True
                db.session.merge(self.model)

        after_record_delete.send(
            current_app._get_current_object(),
            record=self
        )
        return self

    def revert(self, revision_id):
        """Revert the item type to a specific revision.

        #. Send a signal :data:`weko_records.signals.before_record_revert`
           with the current record as parameter.

        #. Revert the record to the revision id passed as parameter.

        #. Send a signal :data:`weko_records.signals.after_record_revert`
           with the reverted record as parameter.

        :param revision_id: Specify the item type revision id
        :returns: The :class:`ItemTypes` instance corresponding to the revision
        id
        """
        if self.model is None:
            raise MissingModelError()

        revision = self.revisions[revision_id]

        with db.session.begin_nested():
            before_record_revert.send(
                current_app._get_current_object(),
                record=self
            )

            self.model.json = dict(revision)

            db.session.merge(self.model)

        after_record_revert.send(
            current_app._get_current_object(),
            record=self
        )
        return self.__class__(self.model.json, model=self.model)

    def restore(self):
        """Restore an logically deleted item type.

        #. Restore the current record.

        :returns: The restored :class:`ItemTypes` instance.
        """
        if self.model is None:
            raise MissingModelError()

        with db.session.begin_nested():
            self.model.is_deleted = False
            db.session.merge(self.model)

        return self

    @property
    def revisions(self):
        """Get revisions iterator."""
        if self.model is None:
            raise MissingModelError()

        return RevisionsIterator(self.model)


class ItemTypeEditHistory(object):
    """Define API for Itemtype Property creation and manipulation."""

    @classmethod
    def create_or_update(cls, id=0, item_type_id=None, user_id=None,
                         notes={}):
        r"""Create or update ItemTypeEditHistory and store it in the database.

        :param id: ID of Itemtype property.
        :param item_type_id: Existing ItemType model id.
        :param user_id: Existing user format.
        :param notes: map of notes in JSON format.
        :returns: A new :class:`` instance.
        """
        with db.session.begin_nested():
            existing = ItemTypeEditHistoryModel.query \
                .filter_by(id=id).one_or_none()
            new_edit_history = existing or \
                ItemTypeEditHistoryModel(
                    item_type_id=item_type_id,
                    user_id=user_id,
                )

            if new_edit_history.notes != notes:
                new_edit_history.notes = notes
                db.session.add(new_edit_history)
        return new_edit_history

    @classmethod
    def get_by_item_type_id(cls, item_type_id):
        """Retrieve record by id.

        :param item_type_id: ItemType id.
        :returns: ItemTypeEditHistory record or None.
        """
        with db.session.no_autoflush:
            return ItemTypeEditHistoryModel.query \
                .filter_by(item_type_id=item_type_id).one_or_none()


class Mapping(RecordBase):
    """Define API for Mapping creation and manipulation."""

    @classmethod
    def create(cls, item_type_id=None, mapping=None):
        r"""Create a new record instance and store it in the database.

        #. Send a signal :data:`weko_records.signals.before_record_insert`
           with the new record as parameter.

        #. Validate the new record data.

        #. Add the new record in the database.

        #. Send a signal :data:`weko_records.signals.after_record_insert`
           with the new created record as parameter.

        :Keyword Arguments:
          * **format_checker** --
            An instance of the class :class:`jsonschema.FormatChecker`, which
            contains validation rules for formats. See
            :func:`~weko_records.api.RecordBase.validate` for more details.

          * **validator** --
            # A :class:`jsonschema.IValidator` class that will be used to
            validate the record. See
            :func:`~weko_records.api.RecordBase.validate` for more details.


        :param item_type_id: ID of item type.
        :param mapping: Mapping in JSON format.
        :returns: A new :class:`Record` instance.
        """
        with db.session.begin_nested():
            record = cls(mapping)

            before_record_insert.send(
                current_app._get_current_object(),
                record=record
            )

            # record.validate(**kwargs)

            record.model = ItemTypeMapping(item_type_id=item_type_id,
                                           mapping=record)

            db.session.add(record.model)

        after_record_insert.send(
            current_app._get_current_object(),
            record=record
        )
        return record

    @classmethod
    def get_record(cls, item_type_id, with_deleted=False):
        """Retrieve the record by id.

        Raise a database exception if the record does not exist.

        :param item_type_id: ID of item type.
        :param with_deleted: If `True` then it includes deleted records.
        :returns: The :class:`Record` instance.
        """
        with db.session.no_autoflush:
            query = ItemTypeMapping.query.filter_by(item_type_id=item_type_id)
            if not with_deleted:
                query = query.filter(ItemTypeMapping.mapping != None)  # noqa
            obj = query.order_by(desc(ItemTypeMapping.created)).first()
            if obj is None:
                return None
            return cls(obj.mapping, model=obj)

    @classmethod
    def get_records(cls, ids, with_deleted=False):
        """Retrieve multiple records by id.

        :param ids: List of record IDs.
        :param with_deleted: If `True` then it includes deleted records.
        :returns: A list of :class:`Record` instances.
        """
        with db.session.no_autoflush:
            query = ItemTypeMapping.query.filter(ItemTypeMapping.id.in_(ids))
            if not with_deleted:
                query = query.filter(ItemTypeMapping.mapping != None)  # noqa
            return [cls(obj.json, model=obj) for obj in query.all()]

    def patch(self, patch):
        """Patch record metadata.

        :params patch: Dictionary of record metadata.
        :returns: A new :class:`Record` instance.
        """
        data = apply_patch(dict(self), patch)
        return self.__class__(data, model=self.model)

    def commit(self, **kwargs):
        r"""Store changes of the current record instance in the database.

        #. Send a signal :data:`weko_records.signals.before_record_update`
           with the current record to be committed as parameter.

        #. Validate the current record data.

        #. Commit the current record in the database.

        #. Send a signal :data:`weko_records.signals.after_record_update`
            with the committed record as parameter.

        :Keyword Arguments:
          * **format_checker** --
            An instance of the class :class:`jsonschema.FormatChecker`, which
            contains validation rules for formats. See
            :func:`~weko_records.api.RecordBase.validate` for more details.

          * **validator** --
            A :class:`jsonschema.IValidator` class that will be used to
            validate the record. See
            :func:`~weko_records.api.RecordBase.validate` for more details.

        :returns: The :class:`Record` instance.
        """
        if self.model is None or self.model.json is None:
            raise MissingModelError()

        with db.session.begin_nested():
            before_record_update.send(
                current_app._get_current_object(),
                record=self
            )

            self.validate(**kwargs)

            self.model.json = dict(self)
            flag_modified(self.model, 'json')

            db.session.merge(self.model)

        after_record_update.send(
            current_app._get_current_object(),
            record=self
        )
        return self

    def delete(self, force=False):
        """Delete a record.

        If `force` is ``False``, the record is soft-deleted: record data will
        be deleted but the record identifier and the history of the record will
        be kept. This ensures that the same record identifier cannot be used
        twice, and that you can still retrieve its history. If `force` is
        ``True``, then the record is completely deleted from the database.

        #. Send a signal :data:`weko_records.signals.before_record_delete`
           with the current record as parameter.

        #. Delete or soft-delete the current record.

        #. Send a signal :data:`weko_records.signals.after_record_delete`
           with the current deleted record as parameter.

        :param force: if ``True``, completely deletes the current record from
               the database, otherwise soft-deletes it.
        :returns: The deleted :class:`Record` instance.
        """
        if self.model is None:
            raise MissingModelError()

        with db.session.begin_nested():
            before_record_delete.send(
                current_app._get_current_object(),
                record=self
            )

            if force:
                db.session.delete(self.model)
            else:
                self.model.json = None
                db.session.merge(self.model)

        after_record_delete.send(
            current_app._get_current_object(),
            record=self
        )
        return self

    def revert(self, revision_id):
        """Revert the record to a specific revision.

        #. Send a signal :data:`weko_records.signals.before_record_revert`
           with the current record as parameter.

        #. Revert the record to the revision id passed as parameter.

        #. Send a signal :data:`weko_records.signals.after_record_revert`
           with the reverted record as parameter.

        :param revision_id: Specify the record revision id
        :returns: The :class:`Record` instance corresponding to the revision id
        """
        if self.model is None:
            raise MissingModelError()

        revision = self.revisions[revision_id]

        with db.session.begin_nested():
            before_record_revert.send(
                current_app._get_current_object(),
                record=self
            )

            self.model.json = dict(revision)

            db.session.merge(self.model)

        after_record_revert.send(
            current_app._get_current_object(),
            record=self
        )
        return self.__class__(self.model.json, model=self.model)

    @property
    def revisions(self):
        """Get revisions iterator."""
        if self.model is None:
            raise MissingModelError()

        return RevisionsIterator(self.model)


class ItemTypeProps(RecordBase):
    """Define API for Itemtype Property creation and manipulation."""

    @classmethod
    def create(cls, property_id=None, name=None, schema=None, form_single=None,
               form_array=None):
        """Create a new ItemTypeProperty instance and store it in the database.

        :param property_id: ID of Itemtype property.
        :param name: Property name.
        :param schema: Property in JSON format.
        :param form_single: form (single) in JSON format.
        :param form_array: form (array) in JSON format.
        :returns: A new :class:`Record` instance.
        """
        with db.session.begin_nested():
            record = cls(schema)

            before_record_insert.send(
                current_app._get_current_object(),
                record=record
            )

            record.model = None
            if property_id > 0:
                obj = ItemTypeProperty.query.filter_by(id=property_id,
                                                       delflg=False).first()
                if obj is not None:
                    obj.name = name
                    obj.schema = schema
                    obj.form = form_single
                    obj.forms = form_array
                    record.model = obj
            if record.model is None:
                record.model = ItemTypeProperty(name=name,
                                                schema=schema,
                                                form=form_single,
                                                forms=form_array)

            db.session.add(record.model)

        after_record_insert.send(
            current_app._get_current_object(),
            record=record
        )
        return record

    @classmethod
    def get_record(cls, property_id):
        """Retrieve the record by id.

        Raise a database exception if the record does not exist.

        :param property_id: ID of item type property.
        :returns: The :class:`Record` instance.
        """
        with db.session.no_autoflush:
            obj = ItemTypeProperty.query.filter_by(id=property_id,
                                                   delflg=False).first()
            if obj is None:
                return None
            cls.helper_remove_empty_required(obj.schema)
            return obj

    @classmethod
    def helper_remove_empty_required(cls, data):
        """Help to remove required key if it is empty.

        Arguments:
            data {dict} -- schema to remove required key
        """
        if "required" in data and not data.get("required"):
            data.pop("required", None)
        if "properties" in data:
            for k, v in data.get("properties").items():
                if v.get("items"):
                    cls.helper_remove_empty_required(v.get("items"))

    @classmethod
    def get_records(cls, ids):
        """Retrieve multiple records by id.

        :param ids: List of record IDs.
        :returns: A list of :class:`Record` instances.
        """
        with db.session.no_autoflush:
            query = None
            if len(ids) > 0:
                query = ItemTypeProperty.query.filter_by(
                    ItemTypeMapping.id.in_(ids))
                query = query.filter_by(delflg=False)  # noqa
            else:
                query = ItemTypeProperty.query.filter_by(delflg=False)

            return query.all()

    @property
    def revisions(self):
        """Get revisions iterator."""
        if self.model is None:
            raise MissingModelError()

        return RevisionsIterator(self.model)


class ItemsMetadata(RecordBase):
    """Define API for ItemsMetadata creation and manipulation."""

    @classmethod
    def create(cls, data, id_=None, **kwargs):
        r"""Create a new record instance and store it in the database.

        #. Send a signal :data:`weko_records.signals.before_record_insert`
           with the new record as parameter.

        #. Validate the new record data.

        #. Add the new record in the database.

        #. Send a signal :data:`weko_records.signals.after_record_insert`
           with the new created record as parameter.

        :Keyword Arguments:
          * **format_checker** --
            An instance of the class :class:`jsonschema.FormatChecker`, which
            contains validation rules for formats. See
            :func:`~weko_records.api.RecordBase.validate` for more details.

          * **validator** --
            A :class:`jsonschema.IValidator` class that will be used to
            validate the record. See
            :func:`~weko_records.api.RecordBase.validate` for more details.

        :param data: Dict with the record metadata.
        :param id_: Specify a UUID to use for the new record, instead of
                    automatically generated.
        :returns: A new :class:`Record` instance.
        """
        with db.session.begin_nested():
            record = cls(data)

            before_record_insert.send(
                current_app._get_current_object(),
                record=record
            )

            # record.validate(**kwargs)

            record.model = ItemMetadata(
                id=id_,
                item_type_id=kwargs.get('item_type_id'),
                json=record
            )

            db.session.add(record.model)

        after_record_insert.send(
            current_app._get_current_object(),
            record=record
        )
        return record

    @classmethod
    def get_record(cls, id_, with_deleted=False):
        """Retrieve the record by id.

        Raise a database exception if the record does not exist.

        :param id_: record ID.
        :param with_deleted: If `True` then it includes deleted records.
        :returns: The :class:`Record` instance.
        """
        with db.session.no_autoflush:
            query = ItemMetadata.query.filter_by(id=id_)
            if not with_deleted:
                query = query.filter(ItemMetadata.json != None)  # noqa
            obj = query.one()
            return cls(obj.json, model=obj)

    @classmethod
    def get_records(cls, ids, with_deleted=False):
        """Retrieve multiple records by id.

        :param ids: List of record IDs.
        :param with_deleted: If `True` then it includes deleted records.
        :returns: A list of :class:`Record` instances.
        """
        with db.session.no_autoflush:
            query = ItemMetadata.query.filter(ItemMetadata.id.in_(ids))
            if not with_deleted:
                query = query.filter(ItemMetadata.json != None)  # noqa

            return [cls(obj.json, model=obj) for obj in query.all()]

    @classmethod
    def get_by_item_type_id(cls, item_type_id, with_deleted=False):
        """Retrieve multiple records by item types identifier.

        :param item_type_id: Identifier of item type.
        :param with_deleted: If `True` then it includes deleted records.
        :returns: A list of :class:`Record` instance.
        """
        with db.session.no_autoflush:
            query = ItemMetadata.query.filter_by(item_type_id=item_type_id)
            if not with_deleted:
                query = query.filter(ItemMetadata.json != None)  # noqa
            return query.all()

    @classmethod
    def get_by_object_id(cls, object_id):
        """Retrieve ItemMetadata data by item identifier.

        :param object_id: Pidstore Identifier of item.
        :returns: A :class:`Record` instance.
        """
        with db.session.no_autoflush:
            query = ItemMetadata.query.filter_by(id=object_id)

            return query.one_or_none()

    def patch(self, patch):
        """Patch record metadata.

        :params patch: Dictionary of record metadata.
        :returns: A new :class:`Record` instance.
        """
        data = apply_patch(dict(self), patch)
        return self.__class__(data, model=self.model)

    def commit(self, **kwargs):
        r"""Store changes of the current record instance in the database.

        #. Send a signal :data:`weko_records.signals.before_record_update`
           with the current record to be committed as parameter.

        #. Validate the current record data.

        #. Commit the current record in the database.

        #. Send a signal :data:`weko_records.signals.after_record_update`
            with the committed record as parameter.

        :Keyword Arguments:
          * **format_checker** --
            An instance of the class :class:`jsonschema.FormatChecker`, which
            contains validation rules for formats. See
            :func:`~weko_records.api.RecordBase.validate` for more details.

          * **validator** --
            A :class:`jsonschema.IValidator` class that will be used to
            validate the record. See
            :func:`~weko_records.api.RecordBase.validate` for more details.

        :returns: The :class:`Record` instance.
        """
        if self.model is None or self.model.json is None:
            raise MissingModelError()

        with db.session.begin_nested():
            before_record_update.send(
                current_app._get_current_object(),
                record=self
            )

            # self.validate(**kwargs)

            self.model.json = dict(self)
            flag_modified(self.model, 'json')

            db.session.merge(self.model)

        after_record_update.send(
            current_app._get_current_object(),
            record=self
        )
        return self

    def delete(self, force=False):
        """Delete a record.

        If `force` is ``False``, the record is soft-deleted: record data will
        be deleted but the record identifier and the history of the record will
        be kept. This ensures that the same record identifier cannot be used
        twice, and that you can still retrieve its history. If `force` is
        ``True``, then the record is completely deleted from the database.

        #. Send a signal :data:`weko_records.signals.before_record_delete`
           with the current record as parameter.

        #. Delete or soft-delete the current record.

        #. Send a signal :data:`weko_records.signals.after_record_delete`
           with the current deleted record as parameter.

        :param force: if ``True``, completely deletes the current record from
               the database, otherwise soft-deletes it.
        :returns: The deleted :class:`Record` instance.
        """
        if self.model is None:
            raise MissingModelError()

        with db.session.begin_nested():
            before_record_delete.send(
                current_app._get_current_object(),
                record=self
            )

            if force:
                db.session.delete(self.model)
            else:
                self.model.json = None
                db.session.merge(self.model)

        after_record_delete.send(
            current_app._get_current_object(),
            record=self
        )
        return self

    def revert(self, revision_id):
        """Revert the record to a specific revision.

        #. Send a signal :data:`weko_records.signals.before_record_revert`
           with the current record as parameter.

        #. Revert the record to the revision id passed as parameter.

        #. Send a signal :data:`weko_records.signals.after_record_revert`
           with the reverted record as parameter.

        :param revision_id: Specify the record revision id
        :returns: The :class:`Record` instance corresponding to the revision id
        """
        if self.model is None:
            raise MissingModelError()

        revision = self.revisions[revision_id]

        with db.session.begin_nested():
            before_record_revert.send(
                current_app._get_current_object(),
                record=self
            )

            self.model.json = dict(revision)

            db.session.merge(self.model)

        after_record_revert.send(
            current_app._get_current_object(),
            record=self
        )
        return self.__class__(self.model.json, model=self.model)

    @property
    def revisions(self):
        """Get revisions iterator."""
        if self.model is None:
            raise MissingModelError()

        return RevisionsIterator(self.model)


class FilesMetadata(RecordBase):
    """Define API for FilesMetadata creation and manipulation."""

    @classmethod
    def create(cls, data, id_=None, **kwargs):
        r"""Create a new record instance and store it in the database.

        #. Send a signal :data:`weko_records.signals.before_record_insert`
           with the new record as parameter.

        #. Validate the new record data.

        #. Add the new record in the database.

        #. Send a signal :data:`weko_records.signals.after_record_insert`
           with the new created record as parameter.

        :Keyword Arguments:
          * **format_checker** --
            An instance of the class :class:`jsonschema.FormatChecker`, which
            contains validation rules for formats. See
            :func:`~weko_records.api.RecordBase.validate` for more details.

          * **validator** --
            A :class:`jsonschema.IValidator` class that will be used to
            validate the record. See
            :func:`~weko_records.api.RecordBase.validate` for more details.

        :param data: Dict with the record metadata.
        :param id_: Specify a UUID to use for the new record, instead of
                    automatically generated.
        :returns: A new :class:`Record` instance.
        """
        with db.session.begin_nested():
            record = cls(data)

            # before_record_insert.send(
            #     current_app._get_current_object(),
            #     record=record
            # )

            # record.validate(**kwargs)

            record.model = FileMetadata(
                pid=kwargs.get('pid'),
                json=record, contents=kwargs.get('con'))

            db.session.add(record.model)

        # after_record_insert.send(
        #     current_app._get_current_object(),
        #     record=record
        # )
        return record

    @classmethod
    def get_record(cls, id_, with_deleted=False):
        """Retrieve the record by id.

        Raise a database exception if the record does not exist.

        :param id_: record ID.
        :param with_deleted: If `True` then it includes deleted records.
        :returns: The :class:`Record` instance.
        """
        with db.session.no_autoflush:
            query = FileMetadata.query.filter_by(pid=id_)
            if not with_deleted:
                query = query.filter(FileMetadata.contents != None)  # noqa
            obj = query.one()
            return cls(obj.json, model=obj)

    @classmethod
    def get_records(cls, ids, with_deleted=False):
        """Retrieve multiple records by id.

        :param ids: List of record IDs.
        :param with_deleted: If `True` then it includes deleted records.
        :returns: A list of :class:`Record` instances.
        """
        with db.session.no_autoflush:
            query = FileMetadata.query.filter_by(pid=ids)
            if not with_deleted:
                query = query.filter(FileMetadata.contents != None)  # noqa

            return [cls(obj.json, model=obj) for obj in query.all()]

    def patch(self, patch):
        """Patch record metadata.

        :params patch: Dictionary of record metadata.
        :returns: A new :class:`Record` instance.
        """
        data = apply_patch(dict(self), patch)
        return self.__class__(data, model=self.model)

    def update_data(id, jsn):
        """Patch record metadata.

        :params patch: Dictionary of record metadata.
        :returns: A new :class:`Record` instance.
        """
        from sqlalchemy import update
        with db.session.begin_nested():
            stmt = update(FileMetadata).where(
                FileMetadata.id == id).values(json=jsn)
            db.session.execute(stmt)

    def commit(self, **kwargs):
        r"""Store changes of the current record instance in the database.

        #. Send a signal :data:`weko_records.signals.before_record_update`
           with the current record to be committed as parameter.

        #. Validate the current record data.

        #. Commit the current record in the database.

        #. Send a signal :data:`weko_records.signals.after_record_update`
            with the committed record as parameter.

        :Keyword Arguments:
          * **format_checker** --
            An instance of the class :class:`jsonschema.FormatChecker`, which
            contains validation rules for formats. See
            :func:`~weko_records.api.RecordBase.validate` for more details.

          * **validator** --
            A :class:`jsonschema.IValidator` class that will be used to
            validate the record. See
            :func:`~weko_records.api.RecordBase.validate` for more details.

        :returns: The :class:`Record` instance.
        """
        if self.model is None or self.model.json is None:
            raise MissingModelError()

        with db.session.begin_nested():
            before_record_update.send(
                current_app._get_current_object(),
                record=self
            )

            self.validate(**kwargs)

            self.model.json = dict(self)
            flag_modified(self.model, 'json')

            db.session.merge(self.model)

        after_record_update.send(
            current_app._get_current_object(),
            record=self
        )
        return self

    def delete(self, force=False):
        """Delete a record.

        If `force` is ``False``, the record is soft-deleted: record data will
        be deleted but the record identifier and the history of the record will
        be kept. This ensures that the same record identifier cannot be used
        twice, and that you can still retrieve its history. If `force` is
        ``True``, then the record is completely deleted from the database.

        #. Send a signal :data:`weko_records.signals.before_record_delete`
           with the current record as parameter.

        #. Delete or soft-delete the current record.

        #. Send a signal :data:`weko_records.signals.after_record_delete`
           with the current deleted record as parameter.

        :param force: if ``True``, completely deletes the current record from
               the database, otherwise soft-deletes it.
        :returns: The deleted :class:`Record` instance.
        """
        if self.model is None:
            raise MissingModelError()

        with db.session.begin_nested():
            before_record_delete.send(
                current_app._get_current_object(),
                record=self
            )

            if force:
                db.session.delete(self.model)
            else:
                self.model.json = None
                db.session.merge(self.model)

        after_record_delete.send(
            current_app._get_current_object(),
            record=self
        )
        return self

    def revert(self, revision_id):
        """Revert the record to a specific revision.

        #. Send a signal :data:`weko_records.signals.before_record_revert`
           with the current record as parameter.

        #. Revert the record to the revision id passed as parameter.

        #. Send a signal :data:`weko_records.signals.after_record_revert`
           with the reverted record as parameter.

        :param revision_id: Specify the record revision id
        :returns: The :class:`Record` instance corresponding to the revision id
        """
        if self.model is None:
            raise MissingModelError()

        revision = self.revisions[revision_id]

        with db.session.begin_nested():
            before_record_revert.send(
                current_app._get_current_object(),
                record=self
            )

            self.model.json = dict(revision)

            db.session.merge(self.model)

        after_record_revert.send(
            current_app._get_current_object(),
            record=self
        )
        return self.__class__(self.model.json, model=self.model)

    @property
    def revisions(self):
        """Get revisions iterator."""
        if self.model is None:
            raise MissingModelError()

        return RevisionsIterator(self.model)


class RecordRevision(RecordBase):
    """API for record revisions."""

    def __init__(self, model):
        """Initialize instance with the SQLAlchemy model."""
        super(RecordRevision, self).__init__(model.json, model=model)


class SiteLicense(RecordBase):
    """Define API for SiteLicense creation and manipulation."""

    @classmethod
    def get_records(cls):
        """Retrieve multiple records.

        :returns: A list of :class:`Record` instances.
        """
        with db.session.no_autoflush:
            sl_obj = SiteLicenseInfo.query.order_by(
                SiteLicenseInfo.organization_id).all()
            return [cls(dict(obj)) for obj in sl_obj]

    @classmethod
    def update(cls, obj):
        """Update method."""
        def get_addr(lst, id_):
            if lst and isinstance(lst, list):
                sld = []
                for j in range(len(lst)):
                    sl = SiteLicenseIpAddress(
                        organization_id=id_,
                        organization_no=j + 1,
                        start_ip_address='.'.join(
                            lst[j].get('start_ip_address')),
                        finish_ip_address='.'.join(
                            lst[j].get('finish_ip_address'))
                    )
                    sld.append(sl)
                return sld

        # update has_site_license field on item type name tbl
        ItemTypeNames.update(obj.get('item_type'))
        site_license = obj.get('site_license')
        if isinstance(site_license, list):
            # delete all rows first
            SiteLicenseIpAddress.query.delete()
            SiteLicenseInfo.query.delete()
            # add new rows
            if site_license:
                sif = []
                for i in range(len(site_license)):
                    lst = site_license[i]
                    if lst.get('mail_address'):
                        receive_mail_flag = lst.get('receive_mail_flag')
                    else:
                        receive_mail_flag = 'F'
                    slif = SiteLicenseInfo(
                        organization_id=i + 1,
                        organization_name=lst.get('organization_name'),
                        receive_mail_flag=receive_mail_flag,
                        mail_address=lst.get('mail_address'),
                        domain_name=lst.get('domain_name'),
                        addresses=get_addr(
                            lst.get('addresses'),
                            i))
                    sif.append(slif)
                # add new rows
                db.session.add_all(sif)
        db.session.commit()


class RevisionsIterator(object):
    """Iterator for record revisions."""

    def __init__(self, model):
        """Initialize instance with the SQLAlchemy model."""
        self._it = None
        self.model = model

    def __len__(self):
        """Get number of revisions."""
        return self.model.versions.count()

    def __iter__(self):
        """Get iterator."""
        self._it = iter(self.model.versions)
        return self

    def next(self):
        """Python 2.7 compatibility."""
        return self.__next__()  # pragma: no cover

    def __next__(self):
        """Get next revision item."""
        return RecordRevision(next(self._it))

    def __getitem__(self, revision_id):
        """Get a specific revision.

        :param revision_id: Specify the record revision id
        """
        return RecordRevision(self.model.versions[revision_id])

    def __contains__(self, revision_id):
        """Test if revision exists.

        :param revision_id: Specify the record revision id
        """
        try:
            self[revision_id]
            return True
        except IndexError:
            return False


class WekoRecord(Record):
    """Weko Record."""

    record_fetcher = staticmethod(weko_record_fetcher)

    @classmethod
    def get_record(cls, pid, id_, with_deleted=False):
        """Retrieve the record by id.

        Raise a database exception if the record does not exist.

        :param id_: record ID.
        :param with_deleted: If `True` then it includes deleted records.
        :returns: The :class:`Record` instance.
        """
        pr = super(WekoRecord, cls).get_record(id_)

        with db.session.no_autoflush:
            query = FileMetadata.query.filter_by(pid=pid)
            if not with_deleted:
                query = query.filter(FileMetadata.contents != None)  # noqa

            return [cls(obj.json, model=obj) for obj in query.all()]

    @property
    def pid(self):
        """Return an instance of record PID."""
        pid = self.record_fetcher(self.id, self)
        return PersistentIdentifier.get(pid.pid_type, pid.pid_value)

    @property
    def depid(self):
        """Return depid of the record."""
        return PersistentIdentifier.get(
            pid_type='depid',
            pid_value=self.get('_deposit', {}).get('id')
        )


class FeedbackMailList(object):
    """Feedback-Mail List API."""

    @classmethod
    def update(cls, item_id, feedback_maillist):
        """Create a new instance feedback_mail_list.

        :param item_id: Item Identifier
        :param feedback_maillist: list mail feedback
        :return boolean: True if success
        """
        try:
            with db.session.begin_nested():
                query_object = _FeedbackMailList.query.filter_by(
                    item_id=item_id).one_or_none()
                if not query_object:
                    query_object = _FeedbackMailList(
                        item_id=item_id,
                        mail_list=feedback_maillist
                    )
                    db.session.add(query_object)
                else:
                    query_object.mail_list = feedback_maillist
                    db.session.merge(query_object)
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            return False
        return True

    @classmethod
    def get_mail_list_by_item_id(cls, item_id):
        """Get a FeedbackMail list by item_id.

        :param item_id:
        :return feedback_mail_list

        """
        try:
            with db.session.no_autoflush:
                query_object = _FeedbackMailList.query.filter_by(
                    item_id=item_id).one_or_none()
                if query_object and query_object.mail_list:
                    return query_object.mail_list
                else:
                    return []
        except SQLAlchemyError:
            return []

    @classmethod
    def delete(cls, item_id):
        """Delete a feedback_mail_list by item_id.

        :param item_id: item_id of target feed_back_mail_list
        :return: bool: True if success
        """
        try:
            with db.session.begin_nested():
                _FeedbackMailList.query.filter_by(
                    item_id=item_id).delete()
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            return False
        return True


class ItemLink(object):
    """Get Community Info."""

    org_item_id = 0

    def __init__(self, pid):
        """Constructor."""
        self.org_item_id = int(pid)

    @classmethod
    def get_item_link_info(cls, pid):
        """Record publish  status change view.

        :param pid: PID object.
        :return: The rendered template.
        """
        from weko_deposit.api import WekoRecord

        dst_relations = ItemReference.get_src_references(pid).all()
        ret = []

        for relation in dst_relations:
            record = WekoRecord.get_record_by_pid(relation.dst_item_pid)
            ret.append(dict(
                item_links=relation.dst_item_pid,
                item_title=record.get('item_title'),
                value=relation.reference_type
            ))

        return ret

    def update(self, items):
        """Record publish  status change view.

        Change record publish status with given status and renders record
        export template.

        :param items: PID object.
        :return: The rendered template.
        """
        dst_relations = ItemReference.get_src_references(
            self.org_item_id).all()
        dst_ids = [dst_item.dst_item_pid for dst_item in dst_relations]
        updated = []
        created = []
        for item in items:
            item_id = item['item_id']
            if item_id in dst_ids:
                updated.extend(item for dst_item in dst_relations if
                               dst_item.reference_type != item['sele_id'])
                dst_ids.remove(item_id)
            else:
                created.append(item)

        deleted = dst_ids
        try:
            with db.session.begin_nested():
                if created:
                    self.bulk_create(created)
                if updated:
                    self.bulk_update(updated)
                if deleted:
                    self.bulk_delete(deleted)
            db.session.commit()
        except SQLAlchemyError as ex:
            current_app.logger.error(ex)
            db.session.rollback()
            return ex
        return None

    def bulk_create(self, dst_items):
        """Record publish  status change view.

        :param pid: PID object.
        :return: The rendered template.
        """
        objects = [ItemReference(
            src_item_pid=self.org_item_id,
            dst_item_pid=cr['item_id'],
            reference_type=cr['sele_id']) for cr in dst_items]
        db.session.bulk_save_objects(objects)

    def bulk_update(self, dst_items):
        """Record publish  status change view.

        :param dst_items: PID object.
        :return: The rendered template.
        """
        objects = [ItemReference(
            src_item_pid=self.org_item_id,
            dst_item_pid=cr['item_id'],
            reference_type=cr['sele_id']) for cr in dst_items]
        for obj in objects:
            db.session.merge(obj)

    def bulk_delete(self, dst_item_ids):
        """Record publish  status change view.

        :param pid: PID object.
        :return: The rendered template.
        """
        for dst_item_id in dst_item_ids:
            db.session.query(ItemReference).filter(
                ItemReference.src_item_pid == self.org_item_id,
                ItemReference.dst_item_pid == dst_item_id
            ).delete(synchronize_session='fetch')
