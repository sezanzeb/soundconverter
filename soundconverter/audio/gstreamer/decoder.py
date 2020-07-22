#!/usr/bin/python3
# -*- coding: utf-8 -*-
#
# SoundConverter - GNOME application for converting between audio formats.
# Copyright 2004 Lars Wirzenius
# Copyright 2005-2017 Gautier Portet
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; version 3 of the License.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA 02111-1307
# USA

from gi.repository import Gst, GObject

from soundconverter.audio.gstreamer.gstreamer import gstreamer_source, Pipeline
from soundconverter.util.fileoperations import vfs_encode_filename
from soundconverter.util.logger import logger


class Decoder(Pipeline):
    """A GstPipeline background task that decodes data and finds tags.

    Used as base for various other gstreamer pipelines."""

    def __init__(self, sound_file):
        Pipeline.__init__(self)
        self.sound_file = sound_file
        self.time = 0
        self.position = 0

        command = '{} location="{}" name=src ! decodebin name=decoder'.format(
            gstreamer_source, vfs_encode_filename(self.sound_file.uri)
        )
        self.add_command(command)
        self.add_signal('decoder', 'pad-added', self.pad_added)

    def have_type(self, typefind, probability, caps):
        pass

    def query_position(self):
        """Ask for the stream position of the current pipeline."""
        try:
            if self.pipeline:
                self.position = max(0, self.pipeline.query_position(
                    Gst.Format.TIME)[1] / Gst.SECOND)
        except Gst.QueryError:
            self.position = 0

    def found_tag(self, decoder, something, taglist):
        """Called when the decoder reads a tag."""
        logger.debug('found_tag: {}'.format(self.sound_file.filename_for_display))
        taglist.foreach(self.append_tag, None)

    def append_tag(self, taglist, tag, unused_udata):
        tag_whitelist = (
            'album-artist',
            'artist',
            'album',
            'title',
            'track-number',
            'track-count',
            'genre',
            'datetime',
            'year',
            'timestamp',
            'album-disc-number',
            'album-disc-count',
        )
        if tag not in tag_whitelist:
            return

        tag_type = Gst.tag_get_type(tag)
        type_getters = {
            GObject.TYPE_STRING: 'get_string',
            GObject.TYPE_DOUBLE: 'get_double',
            GObject.TYPE_FLOAT: 'get_float',
            GObject.TYPE_INT: 'get_int',
            GObject.TYPE_UINT: 'get_uint',
        }

        tags = {}
        if tag_type in type_getters:
            value = str(getattr(taglist, type_getters[tag_type])(tag)[1])
            tags[tag] = value

        if 'datetime' in tag:
            dt = taglist.get_date_time(tag)[1]
            tags['year'] = dt.get_year()
            tags['date'] = dt.to_iso8601_string()[:10]

        logger.debug('    {}'.format(tags))
        self.sound_file.tags.update(tags)

    def pad_added(self, decoder, pad):
        """Called when a decoded pad is created."""
        self.processing = True
        self.query_duration()

    def finished(self):
        Pipeline.finished(self)

    def get_sound_file(self):
        return self.sound_file

    def get_input_uri(self):
        return self.sound_file.uri

    def get_duration(self):
        """Return the total duration of the sound file."""
        return self.sound_file.duration

    def get_position(self):
        """Return the current pipeline position in the stream."""
        self.query_position()
        return self.position
