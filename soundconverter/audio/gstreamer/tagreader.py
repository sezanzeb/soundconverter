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

from gi.repository import GLib

from soundconverter.audio.gstreamer.decoder import Decoder
from soundconverter.audio.gstreamer.gstreamer import Pipeline
from soundconverter.util.logger import logger


class TagReader(Decoder):
    """A GstPipeline background task for finding meta tags in a file."""

    def __init__(self, sound_file):
        Decoder.__init__(self, sound_file)
        self.found_tag_hook = None
        self.found_tags = False
        self.tagread = False
        self.run_start_time = 0
        self.add_command('fakesink')
        self.add_signal(None, 'message::state-changed', self.on_state_changed)
        self.tagread = False

    def set_found_tag_hook(self, found_tag_hook):
        self.found_tag_hook = found_tag_hook

    def on_state_changed(self, bus, message):
        new = message.parse_state_changed()
        if new == Gst.State.PLAYING and not self.tagread:
            self.tagread = True
            logger.debug('TagReading done…')
            self.done()

    def finished(self):
        Pipeline.finished(self)
        self.sound_file.tags_read = True
        if self.found_tag_hook:
            GLib.idle_add(self.found_tag_hook, self)