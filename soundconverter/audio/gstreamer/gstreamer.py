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

import sys
import time

from gi.repository import Gst, Gtk, GLib

from soundconverter.util.fileoperations import vfs_unlink, vfs_exists
from soundconverter.util.task import BackgroundTask
from soundconverter.util.logger import logger
from soundconverter.util.error import show_error


def gtk_iteration():
    while Gtk.events_pending():
        Gtk.main_iteration(False)


def gtk_sleep(duration):
    start = time.time()
    while time.time() < start + duration:
        time.sleep(0.010)
        gtk_iteration()


# load gstreamer audio profiles
_GCONF_PROFILE_PATH = "/system/gstreamer/1.0/audio/profiles/"
_GCONF_PROFILE_LIST_PATH = "/system/gstreamer/1.0/audio/global/profile_list"
audio_profiles_list = []
audio_profiles_dict = {}

try:
    import gi
    gi.require_version('GConf', '2.0')
    from gi.repository import GConf
    _GCONF = GConf.Client.get_default()
    profiles = _GCONF.all_dirs(_GCONF_PROFILE_LIST_PATH)
    for name in profiles:
        if _GCONF.get_bool(_GCONF_PROFILE_PATH + name + "/active"):
            # get profile
            description = _GCONF.get_string(_GCONF_PROFILE_PATH + name + "/name")
            extension = _GCONF.get_string(_GCONF_PROFILE_PATH + name + "/extension")
            pipeline = _GCONF.get_string(_GCONF_PROFILE_PATH + name + "/pipeline")
            # check profile validity
            if not extension or not pipeline:
                continue
            if not description:
                description = extension
            if description in audio_profiles_dict:
                continue
                # store
            profile = description, extension, pipeline
            audio_profiles_list.append(profile)
            audio_profiles_dict[description] = profile
except (ImportError, ValueError):
    pass

required_elements = ('decodebin', 'fakesink', 'audioconvert', 'typefind', 'audiorate')
for element in required_elements:
    if not Gst.ElementFactory.find(element):
        logger.info(("required gstreamer element \'%s\' not found." % element))
        sys.exit(1)

gstreamer_source = 'giosrc'
gstreamer_sink = 'giosink'

# used to dismiss codec installation if the user already canceled it
user_canceled_codec_installation = False

encoders = (
    ('flacenc', 'FLAC', 'flac-enc'),
    ('wavenc', 'WAV', 'wav-enc'),
    ('vorbisenc', 'Ogg Vorbis', 'vorbis-enc'),
    ('oggmux', 'Ogg Vorbis', 'vorbis-mux'),
    ('id3mux', 'MP3 tags', 'mp3-id-tags'),
    ('id3v2mux', 'MP3 tags', 'mp3-id-tags'),
    ('xingmux', 'VBR tags', 'mp3-vbr-tags'),
    ('lamemp3enc', 'MP3', 'mp3-enc'),
    ('faac', 'AAC', 'aac-enc'),
    ('avenc_aac', 'AAC', 'aac-enc'),
    ('mp4mux', 'AAC', 'aac-mux'),
    ('opusenc', 'Opus', 'opus-enc'),
)

available_elements = set()
functions = dict()

for encoder, name, function in encoders:
    have_it = bool(Gst.ElementFactory.find(encoder))
    if have_it:
        available_elements.add(encoder)
    else:
        logger.info('  {} gstreamer element not found'.format(encoder))
    function += '_' + name
    functions[function] = functions.get(function) or have_it

for function in sorted(functions):
    if not functions[function]:
        logger.info('  disabling {} output.'.format(function.split('_')[1]))

if 'oggmux' not in available_elements:
    available_elements.discard('vorbisenc')
if 'mp4mux' not in available_elements:
    available_elements.discard('faac')
    available_elements.discard('avenc_aac')


class Pipeline(BackgroundTask):
    """A background task for running a GstPipeline."""

    def __init__(self):
        BackgroundTask.__init__(self)
        self.pipeline = None
        self.sound_file = None
        self.command = []
        self.parsed = False
        self.signals = []
        self.processing = False
        self.eos = False
        self.error = None
        self.connected_signals = []

    def started(self):
        self.play()

    def cleanup(self):
        for element, sid in self.connected_signals:
            element.disconnect(sid)
        self.connected_signals = []
        self.stop_pipeline()

    def aborted(self):
        self.cleanup()

    def finished(self):
        self.cleanup()

    def add_command(self, command):
        self.command.append(command)

    def add_signal(self, name, signal, callback):
        self.signals.append((name, signal, callback,))

    def toggle_pause(self, paused):
        if not self.pipeline:
            logger.debug('toggle_pause(): pipeline is None !')
            return

        if paused:
            self.pipeline.set_state(Gst.State.PAUSED)
        else:
            self.pipeline.set_state(Gst.State.PLAYING)

    def found_tag(self, decoder, something, taglist):
        pass

    def restart(self):
        self.parsed = False
        self.duration = None
        self.finished()
        if vfs_exists(self.output_filename):
            vfs_unlink(self.output_filename)
        self.play()

    def on_error(self, error):
        self.error = error
        logger.error('{} ({})'.format(error, ' ! '.join(self.command)))

    def on_message_(self, bus, message):
        self.on_message_(bus, message)
        return True

    def on_message(self, bus, message):
        t = message.type
        if t == Gst.MessageType.ERROR:
            error, __ = message.parse_error()
            self.eos = True
            self.error = error
            self.on_error(error)
            self.done()
        elif t == Gst.MessageType.EOS:
            self.eos = True
            self.done()
        elif t == Gst.MessageType.TAG:
            self.found_tag(self, '', message.parse_tag())
        return True

    def play(self):
        """Execute the gstreamer command"""
        if not self.parsed:
            command = ' ! '.join(self.command)
            logger.debug('launching: \'{}\''.format(command))
            try:
                # see https://gstreamer.freedesktop.org/documentation/tools/gst-launch.html
                self.pipeline = Gst.parse_launch(command)
                bus = self.pipeline.get_bus()
                assert not self.connected_signals
                self.connected_signals = []
                for name, signal, callback in self.signals:
                    if name:
                        element = self.pipeline.get_by_name(name)
                    else:
                        element = bus
                    sid = element.connect(signal, callback)
                    self.connected_signals.append((element, sid,))

                self.parsed = True

            except GLib.GError as e:
                show_error('GStreamer error when creating pipeline', str(e))
                self.error = str(e)
                self.eos = True
                self.done()
                return

            bus.add_signal_watch()
            self.watch_id = bus.connect('message', self.on_message)

        self.pipeline.set_state(Gst.State.PLAYING)

    def stop_pipeline(self):
        if not self.pipeline:
            logger.debug('pipeline already stopped!')
            return
        self.pipeline.set_state(Gst.State.NULL)
        bus = self.pipeline.get_bus()
        bus.disconnect(self.watch_id)
        bus.remove_signal_watch()
        self.pipeline = None

    def get_position(self):
        return NotImplementedError

    def query_duration(self):
        """Ask for the duration of the current pipeline."""
        try:
            if not self.sound_file.duration and self.pipeline:
                self.sound_file.duration = self.pipeline.query_duration(Gst.Format.TIME)[1] / Gst.SECOND
                if self.sound_file.duration <= 0:
                    self.sound_file.duration = None
        except Gst.QueryError:
            self.sound_file.duration = None
