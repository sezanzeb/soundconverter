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

import os
import traceback

from urllib.parse import urlparse

from soundconverter.audio import gstreamer
from soundconverter.util.queue import TaskQueue
from soundconverter.util.logger import logger
from soundconverter.util.fileoperations import unquote_filename, vfs_unlink, vfs_rename, vfs_exists, beautify_uri
from soundconverter.util.settings import get_gio_settings
from soundconverter.interface.notify import notification


class Converter():
    """Base class for all converters."""
    


class ConverterQueue(TaskQueue):
    """Background task for converting many files.
    
    Uses one of the available converters"""

    def __init__(self, window, kind=gstreamer.Converter):
        TaskQueue.__init__(self)
        self.Converter = gstreamer.Converter
        self.window = window
        self.overwrite_action = None
        self.reset_counters()

    def reset_counters(self):
        self.duration_processed = 0
        self.overwrite_action = None
        self.errors = []
        self.error_count = 0
        self.all_tasks = None
        global user_canceled_codec_installation
        user_canceled_codec_installation = True

    def add(self, sound_file):
        # generate a temporary filename from source name and output suffix
        output_filename = self.window.prefs.generate_temp_filename(sound_file)

        if vfs_exists(output_filename):
            # always overwrite temporary files
            vfs_unlink(output_filename)

        path = urlparse(output_filename)[2]
        path = unquote_filename(path)

        gio_settings = get_gio_settings()

        c = self.Converter(
            sound_file, output_filename,
            gio_settings.get_string('output-mime-type'),
            gio_settings.get_boolean('delete-original'),
            gio_settings.get_boolean('output-resample'),
            gio_settings.get_int('resample-rate'),
            gio_settings.get_boolean('force-mono'),
        )
        c.set_vorbis_quality(gio_settings.get_double('vorbis-quality'))
        c.set_aac_quality(gio_settings.get_int('aac-quality'))
        c.set_opus_quality(gio_settings.get_int('opus-bitrate'))
        c.set_flac_compression(gio_settings.get_int('flac-compression'))
        c.set_wav_sample_width(gio_settings.get_int('wav-sample-width'))
        c.set_audio_profile(gio_settings.get_string('audio-profile'))

        quality = {
            'cbr': 'mp3-cbr-quality',
            'abr': 'mp3-abr-quality',
            'vbr': 'mp3-vbr-quality'
        }
        mode = gio_settings.get_string('mp3-mode')
        c.set_mp3_mode(mode)
        c.set_mp3_quality(gio_settings.get_int(quality[mode]))
        c.init()
        c.add_listener('finished', self.on_task_finished)
        self.add_task(c)

    def get_progress(self, per_file_progress):
        tasks = self.running_tasks

        # try to get all tasks durations
        if not self.all_tasks:
            self.all_tasks = []
            self.all_tasks.extend(self.waiting_tasks)
            self.all_tasks.extend(self.running_tasks)

        for task in self.all_tasks:
            if task.sound_file.duration is None:
                duration = task.get_duration()

        position = 0.0
        prolist = [1] * self.finished_tasks
        for task in tasks:
            if task.running:
                task_position = task.get_position()
                position += task_position
                per_file_progress[task.sound_file] = None
                if task.sound_file.duration is None:
                    continue
                taskprogress = task_position / task.sound_file.duration
                taskprogress = min(max(taskprogress, 0.0), 1.0)
                prolist.append(taskprogress)
                per_file_progress[task.sound_file] = taskprogress
        for task in self.waiting_tasks:
            prolist.append(0.0)

        progress = sum(prolist) / len(prolist) if prolist else 0
        progress = min(max(progress, 0.0), 1.0)
        return self.running or len(self.all_tasks), progress

    def on_task_finished(self, task):
        task.sound_file.progress = 1.0

        if task.error:
            logger.debug('error in task, skipping rename: {}'.format(task.output_filename))
            if vfs_exists(task.output_filename):
                vfs_unlink(task.output_filename)
            self.errors.append(task.error)
            logger.info('Could not convert {}: {}'.format(beautify_uri(task.get_input_uri()), task.error))
            self.error_count += 1
            return

        duration = task.get_duration()
        if duration:
            self.duration_processed += duration

        # rename temporary file
        newname = self.window.prefs.generate_filename(task.sound_file)
        logger.info('newname {}'.format(newname))
        logger.debug('{} -> {}'.format(beautify_uri(task.output_filename), beautify_uri(newname)))

        # safe mode. generate a filename until we find a free one
        p, e = os.path.splitext(newname)
        p = p.replace('%', '%%')

        space = ' '
        if (get_gio_settings().get_boolean('replace-messy-chars')):
            space = '_'

        p = p + space + '(%d)' + e

        i = 1
        while vfs_exists(newname):
            newname = p % i
            i += 1

        try:
            vfs_rename(task.output_filename, newname)
        except Exception:
            self.errors.append(task.error)
            logger.info('Could not rename {} to {}:'.format(beautify_uri(task.output_filename), beautify_uri(newname)))
            logger.info(traceback.print_exc())
            self.error_count += 1
            return

        logger.info('Converted {} to {}'.format(beautify_uri(task.get_input_uri()), beautify_uri(newname)))

    def finished(self):
        # This must be called with emit_async
        if self.running_tasks:
            raise RuntimeError
        TaskQueue.finished(self)
        self.window.set_sensitive()
        self.window.conversion_ended()
        total_time = self.run_finish_time - self.run_start_time
        msg = _('Conversion done in %s') % self.format_time(total_time)
        if self.error_count:
            msg += ', {} error(s)'.format(self.error_count)
        self.window.set_status(msg)
        if not self.window.is_active():
            notification(msg)  # this must move
        self.reset_counters()

    def format_time(self, seconds):
        units = [(86400, 'd'),
                 (3600, 'h'),
                 (60, 'm'),
                 (1, 's')]
        seconds = round(seconds)
        result = []
        for factor, unity in units:
            count = int(seconds / factor)
            seconds -= count * factor
            if count > 0 or (factor == 1 and not result):
                result.append('{} {}'.format(count, unity))
        assert seconds == 0
        return ' '.join(result)

    def abort(self):
        TaskQueue.abort(self)
        self.window.set_sensitive()
        self.reset_counters()

    def start(self):
        # self.waiting_tasks.sort(key=Converter.get_duration, reverse=True)
        TaskQueue.start(self)