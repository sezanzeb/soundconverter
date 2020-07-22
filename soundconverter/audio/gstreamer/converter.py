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

from gettext import gettext as _

from gi.repository import Gio

from soundconverter.audio.gstreamer.decoder import Decoder
from soundconverter.audio.gstreamer.gstreamer import available_elements, audio_profiles_dict, Pipeline
from soundconverter.util.fileoperations import vfs_unlink, vfs_exists, beautify_uri
from soundconverter.util.logger import logger
from soundconverter.util.error import show_error


class Converter(Decoder):
    """A background task for converting files to another format."""

    def __init__(self, sound_file, output_filename, output_type,
                 delete_original=False, output_resample=False,
                 resample_rate=48000, force_mono=False, ignore_errors=False):
        Decoder.__init__(self, sound_file)

        self.output_filename = output_filename
        self.output_type = output_type
        self.vorbis_quality = 0.6
        self.aac_quality = 192
        self.mp3_bitrate = 192
        self.mp3_mode = 'vbr'
        self.mp3_quality = 3
        self.flac_compression = 8
        self.wav_sample_width = 16

        self.output_resample = output_resample
        self.resample_rate = resample_rate
        self.force_mono = force_mono

        self.overwrite = False
        self.delete_original = delete_original

        self.ignore_errors = ignore_errors

        self.got_duration = False

    def init(self):
        self.encoders = {
            'audio/x-vorbis': self.add_oggvorbis_encoder,
            'audio/x-flac': self.add_flac_encoder,
            'audio/x-wav': self.add_wav_encoder,
            'audio/mpeg': self.add_mp3_encoder,
            'audio/x-m4a': self.add_aac_encoder,
            'audio/ogg; codecs=opus': self.add_opus_encoder,
            'gst-profile': self.add_audio_profile,
        }
        self.add_command('audiorate')
        self.add_command('audioconvert')
        self.add_command('audioresample')

        # audio resampling support
        if self.output_resample:
            self.add_command('audio/x-raw,rate={}'.format(self.resample_rate))
            self.add_command('audioconvert')
            self.add_command('audioresample')

        if self.force_mono:
            self.add_command('audio/x-raw,channels=1')
            self.add_command('audioconvert')

        encoder = self.encoders[self.output_type]()
        self.add_command(encoder)

        gfile = Gio.file_parse_name(self.output_filename)
        dirname = gfile.get_parent()
        if dirname and not dirname.query_exists(None):
            logger.info('Creating folder: \'{}\''.format(beautify_uri(dirname.get_uri())))
            if not dirname.make_directory_with_parents():
                show_error('Error', _("Cannot create \'{}\' folder.").format(beautify_uri(dirname)))
                return

        self.add_command('{} location="{}"'.format(
            gstreamer_sink, encode_filename(self.output_filename)))
        if self.overwrite and vfs_exists(self.output_filename):
            logger.info('overwriting \'{}\''.format(beautify_uri(self.output_filename)))
            vfs_unlink(self.output_filename)

    def aborted(self):
        # remove partial file
        try:
            vfs_unlink(self.output_filename)
        except Exception:
            logger.info('cannot delete: \'{}\''.format(beautify_uri(self.output_filename)))
        return

    def finished(self):
        Pipeline.finished(self)

        # Copy file permissions
        if not Gio.file_parse_name(self.sound_file.uri).copy_attributes(
                Gio.file_parse_name(self.output_filename), Gio.FileCopyFlags.NONE, None):
            logger.info('Cannot set permission on \'{}\''.format(beautify_uri(self.output_filename)))

        if self.delete_original and self.processing and not self.error:
            logger.info('deleting: \'{}\''.format(self.sound_file.uri))
            if not vfs_unlink(self.sound_file.uri):
                logger.info('Cannot remove \'{}\''.format(beautify_uri(self.output_filename)))

    def on_error(self, error):
        if self.ignore_errors:
            self.error = error
            logger.info('ignored-error: {} ({})'.format(error, ' ! '.join(self.command)))
        else:
            Pipeline.on_error(self, error)
            show_error(
                '{}'.format(_('GStreamer Error:')),
                '{}\n({})'.format(error, self.sound_file.filename_for_display)
            )

    def set_vorbis_quality(self, quality):
        self.vorbis_quality = quality

    def set_aac_quality(self, quality):
        self.aac_quality = quality

    def set_opus_quality(self, quality):
        self.opus_quality = quality

    def set_mp3_mode(self, mode):
        self.mp3_mode = mode

    def set_mp3_quality(self, quality):
        self.mp3_quality = quality

    def set_flac_compression(self, compression):
        self.flac_compression = compression

    def set_wav_sample_width(self, sample_width):
        self.wav_sample_width = sample_width

    def set_audio_profile(self, audio_profile):
        self.audio_profile = audio_profile

    def add_flac_encoder(self):
        s = 'flacenc mid-side-stereo=true quality={}'.format(self.flac_compression)
        return s

    def add_wav_encoder(self):
        formats = {8: 'U8', 16: 'S16LE', 24: 'S24LE', 32: 'S32LE'}
        return 'audioconvert ! audio/x-raw,format={} ! wavenc'.format(formats[self.wav_sample_width])

    def add_oggvorbis_encoder(self):
        cmd = 'vorbisenc'
        if self.vorbis_quality is not None:
            cmd += ' quality={}'.format(self.vorbis_quality)
        cmd += ' ! oggmux '
        return cmd

    def add_mp3_encoder(self):
        cmd = 'lamemp3enc encoding-engine-quality=2 '

        if self.mp3_mode is not None:
            properties = {
                'cbr': 'target=bitrate cbr=true bitrate=%s ',
                'abr': 'target=bitrate cbr=false bitrate=%s ',
                'vbr': 'target=quality cbr=false quality=%s ',
            }

            cmd += properties[self.mp3_mode] % self.mp3_quality

            if 'xingmux' in available_elements and self.mp3_mode != 'cbr':
                # add xing header when creating VBR/ABR mp3
                cmd += '! xingmux '

        if 'id3mux' in available_elements:
            # add tags
            cmd += '! id3mux '
        elif 'id3v2mux' in available_elements:
            # add tags
            cmd += '! id3v2mux '

        return cmd

    def add_aac_encoder(self):
        encoder = 'faac' if 'faac' in available_elements else 'avenc_aac'
        return '{} bitrate={} ! mp4mux'.format(encoder, self.aac_quality * 1000)

    def add_opus_encoder(self):
        return 'opusenc bitrate={} bitrate-type=vbr bandwidth=auto ! oggmux'.format(self.opus_quality * 1000)

    def add_audio_profile(self):
        # TODO what is this
        # TODO is this gstreamer related
        print('audio_profiles_dict', audio_profiles_dict, self.audio_profile)
        pipeline = audio_profiles_dict[self.audio_profile][2]
        return pipeline