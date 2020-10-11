#!/usr/bin/python3
# -*- coding: utf-8 -*-
#
# SoundConverter - GNOME application for converting between audio formats.
# Copyright 2004 Lars Wirzenius
# Copyright 2005-2020 Gautier Portet
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


import traceback


class Task:
    error = None
    timer = None

    """Abstract class of a single task."""
    # avoid storing a variable called timer in your inheriting class
    def get_progress(self):
        """Fraction of how much of the task is completed.

        Returns a tuple of (progress, weight), because some tasks may
        take longer than others (because it processes more audio), which
        cannot be reflected by the progress alone. The weight might
        correspond to the length of the audio files for example.
        """
        raise NotImplementedError()

    def cancel(self):
        """Cancel the execution of the task."""
        raise NotImplementedError()

    def pause(self):
        """Pause the execution of the task."""
        raise NotImplementedError()

    def resume(self):
        """Resume the execution of the task after pausing."""
        raise NotImplementedError()

    def run(self):
        """Run the task."""
        raise NotImplementedError()

    def callback(self):
        """Should be overwritten by set_callback."""
        pass

    # don't overwrite

    def set_callback(self, callback):
        """To execute a function when the task has ended.

        Make sure to call self.callback() when your task that inherits from
        Task has ended.

        Parameters
        ----------
        callback : function
            A callback that is used to continue the execution of your logic
            when your code needs to wait for the tasks end first.
        """
        def callback_wrapped():
            # automatically provide self as argument, so that
            # it's only required to call callback() without any argument
            return callback(self)
        self.callback = callback_wrapped

    def __getattribute__(self, name):
        """Wrap every function call in order to skip failing tasks.

        So that single edge-case tasks don't screw up the whole application.
        """
        attr = object.__getattribute__(self, name)
        if callable(attr):
            def callback_on_error(*args, **kwargs):
                try:
                    return attr(*args, **kwargs)
                except Exception as e:
                    # tell the taskqueue to go on, otherwise it will never
                    # complete and use one parallel job less. But only do
                    # that if the exception was never handled.
                    if not hasattr(e, 'uncaught_hook'):
                        # only keep the uncaught_hook of the innermost callback,
                        # because it will bubble through the callback of the previous task
                        # that caused the queue to run the next task.
                        e.uncaught_hook = self.callback
                    raise e
            return callback_on_error
        return object.__getattribute__(self, name)
