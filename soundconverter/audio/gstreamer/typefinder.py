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

from fnmatch import fnmatch

from soundconverter.audio.gstreamer.gstreamer import gstreamer_source, Pipeline
from soundconverter.util.fileoperations import vfs_encode_filename
from soundconverter.util.logger import logger
from soundconverter.util.formats import mime_whitelist, filename_blacklist


class TypeFinder(Pipeline):
    def __init__(self, sound_file, silent=False):
        Pipeline.__init__(self)
        self.sound_file = sound_file

        command = '{} location="{}" ! decodebin name=decoder ! fakesink'.format(
            gstreamer_source, vfs_encode_filename(self.sound_file.uri)
        )
        self.add_command(command)
        self.add_signal('decoder', 'pad-added', self.pad_added)
        # 'typefind' is the name of the typefind element created inside
        # decodebin. we can't use our own typefind before decodebin anymore,
        # since its caps would've been the same as decodebin's sink caps.
        self.add_signal('typefind', 'have-type', self.have_type)
        self.silent = silent

    def log(self, msg):
        """Print a line to the console, but only when the TypeFinder itself is not set to silent.

        It can also be disabled with the -q command line option.
        """
        if not self.silent:
            logger.info(msg)

    def on_error(self, error):
        self.error = error
        self.log('ignored-error: {} ({})'.format(error, ' ! '.join(self.command)))

    def set_found_type_hook(self, found_type_hook):
        self.found_type_hook = found_type_hook

    def pad_added(self, decoder, pad):
        """Called when a decoded pad is created."""
        self.query_duration()
        self.done()

    def have_type(self, typefind, probability, caps):
        mime_type = caps.to_string()
        logger.debug('have_type: {} {}'.format(mime_type, self.sound_file.filename_for_display))
        self.sound_file.mime_type = None
        for t in mime_whitelist:
            if t in mime_type:
                self.sound_file.mime_type = mime_type
        if not self.sound_file.mime_type:
            self.log('mime type skipped: {}'.format(mime_type))
        for t in filename_blacklist:
            if fnmatch(self.sound_file.uri, t):
                self.sound_file.mime_type = None
                self.log('filename blacklisted ({}): {}'.format(t, self.sound_file.filename_for_display))

        return True

    def finished(self):
        Pipeline.finished(self)
        if self.error:
            return
        if self.found_type_hook and self.sound_file.mime_type:
            self.found_type_hook(self.sound_file, self.sound_file.mime_type)
            self.sound_file.mime_type = True
            