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
        # Overlay graphics
        self.overlay_file = ffmpeg.input(c.OVERLAY_FILE, loop=1, t=4)
        if c.OVERLAY_FILE_OUTLINE:
            self.overlay_file_outline = ffmpeg.input(c.OVERLAY_FILE_OUTLINE, loop=1, t=4)

    def start(self):
        Logger.LOGGER.log(Logger.TYPE_INFO,
                          f'Starting Server, output to: {self.output}')

        # Read from stdin (pipe)
        in1 = ffmpeg.input('pipe:')

        # Draw clock
        v_live = ffmpeg.drawtext(
            in1['v'], '%{localtime:%R}',
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
        v_live = ffmpeg.overlay(v_live, self.overlay_file, x=c.OVERLAY_X, y=c.OVERLAY_Y)
        if c.OVERLAY_FILE_OUTLINE:
            v_live = ffmpeg.overlay(v_live, self.overlay_file_outline, x=c.OVERLAY_X, y=c.OVERLAY_Y)

        a_live = in1['a']

        # Create black screen input
        black = ffmpeg.input(
            f'color=c=black:s={c.SERV_OUTPUT_WIDTH}x{c.SERV_OUTPUT_HEIGHT}:d=2:r={c.SERV_OUTPUT_FPS}', f='lavfi'
        )

        # Scale and match frame rate of live video to ensure xfade works
        v_live_scaled = ffmpeg.filter(v_live, 'scale', c.SERV_OUTPUT_WIDTH, c.SERV_OUTPUT_HEIGHT)
        v_live_scaled = ffmpeg.filter(v_live_scaled, 'fps', fps=c.SERV_OUTPUT_FPS)

        # Crossfade black screen to live video
        v_final = ffmpeg.filter([black, v_live_scaled], 'xfade', transition='fade', duration=1, offset=1)

        # Combine audio with video (audio starts immediately)
        joined = ffmpeg.concat(v_final, a_live, v=1, a=1)

        # OUTPUT: HLS (stream.m3u8 + rolling .ts segments)
        self.ff = ffmpeg.output(
            joined,
            self.output,
            format='hls',
            hls_time='4',
            hls_list_size='10',
            hls_flags='delete_segments+append_list',
            hls_segment_filename='/dev/shm/hls/stream%d.ts',
            vcodec=c.SERV_OUTPUT_VCODEC,
            pix_fmt=c.PIX_FMT,
            aspect=c.SERV_OUTPUT_ASPECT,
            crf=c.SERV_OUTPUT_CRF,
            tune='zerolatency',
            acodec=c.SERV_OUTPUT_ACODEC,
            preset=c.PRESET
        )

        # Build FFmpeg command
        self.cmd = ['ffmpeg'] + ffmpeg.get_args(self.ff)

        # Launch FFmpeg process
        self.process = subprocess.Popen(
            self.cmd,
            stdin=subprocess.PIPE,
            stdout=devnull,
            stderr=(None if SERVER_DEBUG else devnull)
        )

        Logger.LOGGER.log(Logger.TYPE_INFO, 'Server Process Created')
        return self.process
