import ffmpeg
import subprocess
import Logger
import Config as c

SERVER_DEBUG = True
devnull = subprocess.DEVNULL

class Server:

    def __init__(self, output):
        self.ff = ''
        self.process = None
        self.output = output

        # Preload overlays (looped, short duration so FFmpeg can cache them)
        self.overlay_file = ffmpeg.input(c.OVERLAY_FILE, loop=1, t=4)
        self.overlay_file_outline = None
        if c.OVERLAY_FILE_OUTLINE:
            self.overlay_file_outline = ffmpeg.input(c.OVERLAY_FILE_OUTLINE, loop=1, t=4)

    def start(self):
        Logger.LOGGER.log(Logger.TYPE_INFO, f'Starting Server, output to: {self.output}')

        # Read from stdin (pipe)
        in1 = ffmpeg.input('pipe:')

        # Video chain: draw clock
        v = ffmpeg.drawtext(
            in1['v'],
            '%{localtime:%R}',
            x=c.SERV_DRAWTEXT_X,
            y=c.SERV_DRAWTEXT_Y,
            escape_text=False,
            shadowcolor=c.SERV_DRAWTEXT_SHADOW_COLOR,
            shadowx=c.SERV_DRAWTEXT_SHADOW_X,
            shadowy=c.SERV_DRAWTEXT_SHADOW_Y,
            fontsize=c.SERV_DRAWTEXT_FONT_SIZE,
            fontfile=c.SERV_DRAWTEXT_FONT_FILE,
            fontcolor=c.SERV_DRAWTEXT_FONT_COLOR
        )

        # Overlay graphics
        v = ffmpeg.overlay(v, self.overlay_file, x=c.OVERLAY_X, y=c.OVERLAY_Y)
        if self.overlay_file_outline is not None:
            v = ffmpeg.overlay(v, self.overlay_file_outline, x=c.OVERLAY_X, y=c.OVERLAY_Y)

        # Audio directly from input
        a = in1['a']

        # Build output with low-latency options, no concat
        self.ff = ffmpeg.output(
            v,
            a,
            self.output,  # stream.m3u8
            format='hls',
            # HLS latency tuning
            hls_time='1',                     # shorter segments
            hls_list_size='3',                # fewer segments in playlist
            hls_flags='delete_segments+append_list+omit_endlist+independent_segments',
            hls_segment_filename='/dev/shm/hls/stream%d.ts',

            # Video encoding
            vcodec=c.SERV_OUTPUT_VCODEC,
            pix_fmt=c.PIX_FMT,
            aspect=c.SERV_OUTPUT_ASPECT,
            crf=c.SERV_OUTPUT_CRF,
            preset=c.PRESET,                  # e.g. 'ultrafast' for lowest latency
            tune='zerolatency',

            # Audio encoding
            acodec=c.SERV_OUTPUT_ACODEC,

            # Low-latency demux/IO flags
            fflags='nobuffer',
            flags='low_delay',
            max_delay='0',
            avioflags='direct'
        )

        # Build FFmpeg command
        self.cmd = ['ffmpeg'] + ffmpeg.get_args(self.ff)

        # Launch FFmpeg
        self.process = subprocess.Popen(
            self.cmd,
            stdin=subprocess.PIPE,
            stdout=devnull,
            stderr=(None if SERVER_DEBUG else devnull)
        )

        Logger.LOGGER.log(Logger.TYPE_INFO, 'Server Process Created')
        return self.process
