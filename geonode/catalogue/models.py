# -*- coding: utf-8 -*-
#########################################################################
#
# Copyright (C) 2016 OSGeo
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#
#########################################################################

import errno
import logging

from django.conf import settings
from django.db.models import signals
from lxml import etree
from defusedxml import lxml as dlxml
from geonode.layers.models import Layer
from geonode.documents.models import Document
from geonode.catalogue import get_catalogue
from geonode.base.models import Link, ResourceBase


LOGGER = logging.getLogger(__name__)


def catalogue_pre_delete(instance, sender, **kwargs):
    """Removes the layer from the catalogue"""
    catalogue = get_catalogue()
    catalogue.remove_record(instance.uuid)


def catalogue_post_save(instance, sender, **kwargs):
    """Get information from catalogue"""

    # if layer is not to be published, temporarily
    # change publish state to be able to update
    # properties (#2332)
    is_published = instance.is_published
    resources = ResourceBase.objects.filter(id=instance.resourcebase_ptr.id)

    # Trmporarly enable the Resources
    if not is_published:
        resources.update(is_published=True)

    # Update the Catalog
    try:
        try:
            catalogue = get_catalogue()
            catalogue.create_record(instance)
            record = catalogue.get_record(instance.uuid)
        except EnvironmentError as err:
            msg = 'Could not connect to catalogue to save information for layer "%s"' % instance.name
            if err.reason.errno == errno.ECONNREFUSED:
                LOGGER.warn(msg, err)
                return
            else:
                raise err

        msg = ('Metadata record for %s does not exist,'
               ' check the catalogue signals.' % instance.title)
        assert record is not None, msg

        msg = ('Metadata record for %s should contain links.' % instance.title)
        assert hasattr(record, 'links'), msg

        # Create the different metadata links with the available formats
        for mime, name, metadata_url in record.links['metadata']:
            try:
                Link.objects.get_or_create(resource=instance.resourcebase_ptr,
                                           url=metadata_url,
                                           defaults=dict(name=name,
                                                         extension='xml',
                                                         mime=mime,
                                                         link_type='metadata')
                                           )
            except Link.MultipleObjectsReturned:
                _d = dict(name=name,
                          extension='xml',
                          mime=mime,
                          link_type='metadata')
                Link.objects.filter(resource=instance.resourcebase_ptr,
                                    url=metadata_url,
                                    extension='xml',
                                    link_type='metadata').update(**_d)

        # generate an XML document (GeoNode's default is ISO)
        if instance.metadata_uploaded and instance.metadata_uploaded_preserve:
            md_doc = etree.tostring(dlxml.fromstring(instance.metadata_xml))
        else:
            md_doc = catalogue.catalogue.csw_gen_xml(instance, 'catalogue/full_metadata.xml')

        csw_anytext = catalogue.catalogue.csw_gen_anytext(md_doc)

        csw_wkt_geometry = instance.geographic_bounding_box.split(';')[-1]

        resources = ResourceBase.objects.filter(id=instance.resourcebase_ptr.id)

        resources.update(metadata_xml=md_doc)
        resources.update(csw_wkt_geometry=csw_wkt_geometry)
        resources.update(csw_anytext=csw_anytext)
    finally:
        # Revert temporarily changed publishing state
        if not is_published:
            resources.update(is_published=is_published)


def catalogue_pre_save(instance, sender, **kwargs):
    """Send information to catalogue"""
    return

    # no idea why this was removed in notifications branch
    record = None

    # if the layer is in the catalogue, try to get the distribution urls
    # that cannot be precalculated.
    try:
        catalogue = get_catalogue()
        record = catalogue.get_record(instance.uuid)
    except EnvironmentError as err:
        msg = 'Could not connect to catalogue to save information for layer "%s"' % instance.name
        LOGGER.warn(msg, err)
        raise err

    if record is None:
        return


if 'geonode.catalogue' in settings.INSTALLED_APPS:
    signals.pre_save.connect(catalogue_pre_save, sender=Layer)
    signals.post_save.connect(catalogue_post_save, sender=Layer)
    signals.pre_delete.connect(catalogue_pre_delete, sender=Layer)
    signals.pre_save.connect(catalogue_pre_save, sender=Document)
    signals.post_save.connect(catalogue_post_save, sender=Document)
    signals.pre_delete.connect(catalogue_pre_delete, sender=Document)
