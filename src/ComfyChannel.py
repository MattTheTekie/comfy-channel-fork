#!/usr/bin/env python3

import os
import sys
import json
import random
import signal
import subprocess
import configparser
from os import listdir, getpid
from datetime import datetime, timedelta
from os.path import exists
import psutil

import ffmpeg
import pymediainfo
from colorama import Back, Fore, Style

# ========================
# Config (merged from Config.py)
# ========================
TIME_INDEX = None

# Desired Resolution
W = 854
H = 480

MAX_SAME_FILE_RETRIES = 3
MAX_CONSECUTIVE_RETRIES = 3

PLAYOUT_FILE = 'playout.ini'
TRACKER_FILE = 'comfy-tracker.json'
OUTPUT_LOCATION = '/dev/shm/hls/stream.m3u8'
LOOP = True

SCHEDULER_UPNEXT_VIDEO_FOLDER = 'upnext/video'
SCHEDULER_UPNEXT_AUDIO_FOLDER = 'upnext/audio'
SCHEDULER_UPNEXT_WISDOM_FILE = 'upnext/wisdom.txt'

BUMP_FOLDER = 'bumpers'

OVERLAY_FILE = 'upnext/comfychan.png'
OVERLAY_FILE_OUTLINE = False
OVERLAY_X = W - 50
OVERLAY_Y = 0

EXCLUDED_FILETYPES = ['srt', 'ass', 'idx', 'sub', 'py']
EXCLUDED_DIRNAMES = ['Specials']

# Server & Client shared settings
PIX_FMT = 'yuv420p'
PRESET = 'ultrafast'

SERV_DRAWTEXT_X = 25
SERV_DRAWTEXT_Y = 25
SERV_DRAWTEXT_SHADOW_X = 2
SERV_DRAWTEXT_SHADOW_Y = 2
SERV_DRAWTEXT_SHADOW_COLOR = 'black'
SERV_DRAWTEXT_FONT_FILE = 'fonts/hc-too5.ttf'
SERV_DRAWTEXT_FONT_SIZE = 20
SERV_DRAWTEXT_FONT_COLOR = 'white'

SERV_OUTPUT_VCODEC = 'h264'
SERV_OUTPUT_ASPECT = f"{W}:{H}"
SERV_OUTPUT_CRF = 18
SERV_OUTPUT_ACODEC = 'aac'
SERV_OUTPUT_FORMAT = 'flv'

CLIENT_DRAWTEXT_X = 25
CLIENT_DRAWTEXT_Y = 90
CLIENT_DRAWTEXT_SHADOW_X = 2
CLIENT_DRAWTEXT_SHADOW_Y = 2
CLIENT_DRAWTEXT_SHADOW_COLOR = 'black'
CLIENT_DRAWTEXT_FONT_FILE = 'fonts/hc-too5.ttf'
CLIENT_DRAWTEXT_FONT_SIZE = 16
CLIENT_DRAWTEXT_FONT_COLOR = 'white'

CLIENT_VCODEC = 'h264'
CLIENT_ASPECT = f"{W}:{H}"
CLIENT_FLAGS = '+cgop'
CLIENT_G = 25
CLIENT_ACODEC = 'aac'
CLIENT_STRICT = 1
CLIENT_AUDIO_BITRATE = '168k'
CLIENT_AUDIO_RATE = 44100
CLIENT_HLS_ALLOW_CACHE = 0
CLIENT_HLS_TIME = 3
CLIENT_HLS_LIST_SIZE = 5
CLIENT_FORMAT = 'hls'
CLIENT_FLEX = 3
CLIENT_ENABLE_DEINTERLACE = True

# ========================
# Logger
# ========================
TYPE_INFO = 1
TYPE_ERROR = 2
TYPE_CRIT = 3

def get_time():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

class LoggerClass:
    def __init__(self, log_file=None):
        self.log_file = log_file

    def log(self, log_type, text):
        if log_type == TYPE_INFO:
            print(Fore.GREEN+get_time()+" INFO"+Style.RESET_ALL+': {}'.format(text))
        if log_type == TYPE_ERROR:
            print(Fore.YELLOW+get_time()+" ERROR"+Style.RESET_ALL+': {}'.format(text))
        if log_type == TYPE_CRIT:
            print(Fore.RED+get_time()+" CRITICAL"+Style.RESET_ALL+': {}'.format(text))

LOGGER = LoggerClass()

# ========================
# MediaItem
# ========================
class MediaItem:
    def __init__(self, video_path, audio_path=None, media_type="regular", overlay_text=None, subtitles=0, audio_track=False):
        self.video_path = video_path
        self.audio_path = audio_path
        self.media_type = media_type
        self.overlay_text = overlay_text
        self.audio_track = 0 if audio_track is False else audio_track

        self.media_info = pymediainfo.MediaInfo.parse(self.video_path)

        self.force_english = False
        self.subtitle_file = False
        self.subtitle_format = False
        self.subtitle_track = False

        langs = [t.to_data().get('language') for t in self.media_info.tracks if t.track_type == "Audio"]
        if 'en' in langs and 'ja' in langs and audio_track is False:
            self.force_english = True

        if subtitles >= 1:
            for track in self.media_info.tracks:
                if track.track_type == "Text" and video_path.endswith("mkv"):
                    self.subtitle_file = self.video_path
                    self.subtitle_track = subtitles - 1
                    self.subtitle_format = track.format.lower()
                    break
            if not self.subtitle_file:
                for ext in ['ass', 'srt', 'sub', 'pgs']:
                    if exists(video_path[:-3] + ext):
                        self.subtitle_file = video_path[:-3] + ext
                        self.subtitle_format = ext
                        break
            if not self.subtitle_file:
                LOGGER.log(TYPE_ERROR, f'No subs found for file: {self.video_path}')

        self.title = self.media_info.tracks[0].file_name if not self.media_info.tracks[0].other_file_name else self.media_info.tracks[0].other_file_name[0]
        self.duration = self.media_info.tracks[0].duration or 0
        self.duration_readable = timedelta(milliseconds=int(float(self.duration)))
        self.file_extension = self.media_info.tracks[0].file_extension

    def __str__(self):
        if self.media_type == "upnext":
            return (self.video_path, self.audio_path, self.overlay_text)
        else:
            return self.video_path

# ========================
# Generator Functions
# ========================
def listdir_nohidden(path):
    for f in os.listdir(path):
        if not f.startswith('.'):
            yield os.path.join(path, f)

def listdir_file_walk(dir):
    listing = []
    for path, dirs, files in os.walk(dir):
        files = [f for f in files if not f.startswith('.') and not f.endswith('.part')]
        dirs[:] = [d for d in dirs if not d.startswith('.')]
        for f in files:
            listing.append(os.path.join(path, f))
    return listing

def get_tracker_val(dir):
    with open(TRACKER_FILE, 'r') as f:
        j = json.load(f)
        return j[dir]

def set_tracker_val(dir, val):
    with open(TRACKER_FILE, 'r') as f:
        j = json.load(f)
    j[dir] = val
    with open(TRACKER_FILE, 'w') as f:
        json.dump(j, f, indent=4)

def gen_playlist(dir, mode="sequential", num_files=None, subtitles=0, audio_track=False):
    playlist = []
    if mode == "single":
        LOGGER.log(TYPE_INFO, f'Generating playlist from single file: {dir}')
        playlist.append(MediaItem(dir, subtitles=subtitles, audio_track=audio_track))
        return playlist

    LOGGER.log(TYPE_INFO, f'Generating playlist from directory: {dir}')
    directory_listing = []
    x = 0

    for path, dirs, files in os.walk(dir):
        dirs.sort()
        files.sort()
        files = [f for f in files if not f.startswith('.') and f.split('.')[-1] not in EXCLUDED_FILETYPES]
        if os.path.basename(path) not in EXCLUDED_DIRNAMES:
            for name in files:
                directory_listing.append(os.path.join(path, name))

    if num_files is None:
        num_files = len(directory_listing)

    if mode == "shuffle":
        random.SystemRandom().shuffle(directory_listing)
    elif mode == "tracker":
        try: x = get_tracker_val(dir)
        except KeyError: set_tracker_val(dir, 0)
        set_tracker_val(dir, x + num_files)

    difference = (x + num_files) - len(directory_listing)
    if difference > 0:
        if mode == "tracker": set_tracker_val(dir, difference)
        playlist.extend([MediaItem(i, subtitles=subtitles, audio_track=audio_track) for i in directory_listing[x:x+(num_files-difference)]])
        playlist.extend([MediaItem(i, subtitles=subtitles, audio_track=audio_track) for i in directory_listing[0:difference]])
    else:
        playlist.extend([MediaItem(i, subtitles=subtitles, audio_track=audio_track) for i in directory_listing[x:x+num_files]])

    return playlist

def gen_upnext(video_dir, audio_dir=None, name=None, playlist=None, info_file=None):
    video_file = random.SystemRandom().choice(list(listdir_nohidden(video_dir)))
    audio_file = random.SystemRandom().choice(listdir_file_walk(audio_dir))
    info_text = None
    if playlist:
        info_text = gen_upnext_text(playlist, name, info_file=info_file, duration=pymediainfo.MediaInfo.parse(video_file).tracks[0].duration/1000)
    return MediaItem(video_path=video_file, audio_path=audio_file, media_type="upnext", overlay_text=info_text)

def gen_upnext_text(playlist, name=None, info_file=None, duration=0):
    overlay_text = ""
    if name: overlay_text += name + "\n\n"
    global TIME_INDEX
    TIME_INDEX += timedelta(seconds=duration)
    for idx, item in enumerate(playlist):
        overlay_text += ('Next - ' if idx==0 else TIME_INDEX.strftime("%H:%M")+' - ') + "  "+item.title+"\n\n"
        TIME_INDEX += timedelta(seconds=(item.duration/1000))
    if info_file:
        overlay_text += "\n" + get_random_line(info_file)
    return overlay_text

def gen_music_playlist(dir, num_files=5):
    playlist = []
    directory_listing = []
    LOGGER.log(TYPE_INFO, f'Generating music playlist from directory: {dir}')
    for path, dirs, files in os.walk(dir):
        files.sort()
        files = [f for f in files if not f.startswith('.') and f.split('.')[-1] not in EXCLUDED_FILETYPES]
        if os.path.basename(path) not in EXCLUDED_DIRNAMES:
            for name in files: directory_listing.append(os.path.join(path, name))
    random.SystemRandom().shuffle(directory_listing)
    playlist.extend([MediaItem(i, media_type="music") for i in directory_listing[:num_files]])
    return playlist

def just_advance_timeindex(playlist):
    global TIME_INDEX
    for item in playlist:
        TIME_INDEX += timedelta(seconds=(item.duration/1000))

def get_random_line(file):
    with open(file) as f:
        line = random.SystemRandom().choice(f.readlines())
    return line + "\n"

# ========================
# Client Class
# ========================
CLIENT_DEBUG = True
devnull = subprocess.DEVNULL

class Client:
    def __init__(self, media_item, server):
        self.ff = ''
        self.cmd = ''
        self.media_item = media_item
        self.media_type = media_item.media_type
        self.process = None
        self.server = server

    def play(self):
        output_stream = None

        if self.media_type == "upnext":
            LOGGER.log(TYPE_INFO, f'Playing upnext v:{self.media_item.video_path} a:{self.media_item.audio_path} (Duration: {self.media_item.duration_readable})')
            in1 = ffmpeg.input(self.media_item.video_path)
            in2 = ffmpeg.input(self.media_item.audio_path)
            v1 = ffmpeg.filter(in1['v'], 'scale', w=W, h=H, force_original_aspect_ratio="decrease")
            v1 = ffmpeg.filter(v1, 'pad', w=W, h=H, x='(ow-iw)/2', y='(oh-ih)/2')
            v1 = ffmpeg.drawtext(v1, '{}'.format(self.media_item.overlay_text),
                                 x=CLIENT_DRAWTEXT_X, y=CLIENT_DRAWTEXT_Y,
                                 escape_text=False,
                                 shadowcolor=CLIENT_DRAWTEXT_SHADOW_COLOR,
                                 shadowx=CLIENT_DRAWTEXT_SHADOW_X,
                                 shadowy=CLIENT_DRAWTEXT_SHADOW_Y,
                                 fontsize=CLIENT_DRAWTEXT_FONT_SIZE,
                                 fontfile=CLIENT_DRAWTEXT_FONT_FILE,
                                 fontcolor=CLIENT_DRAWTEXT_FONT_COLOR)
            a1 = in1['a']; a2 = in2['a']
            audio_join = ffmpeg.filter([a1, a2], 'amix', duration="first")
            output_stream = ffmpeg.concat(v1, audio_join, v=1, a=1)
        else:
            LOGGER.log(TYPE_INFO, f'Playing v:{self.media_item} (Duration: {self.media_item.duration_readable})')
            in1 = ffmpeg.input(self.media_item.video_path)
            v1 = ffmpeg.filter(in1['v'], 'scale', w=W, h=H, force_original_aspect_ratio="decrease")
            v1 = ffmpeg.filter(v1, 'pad', w=W, h=H, x='(ow-iw)/2', y='(oh-ih)/2')
            if CLIENT_ENABLE_DEINTERLACE: v1 = ffmpeg.filter(v1, 'yadif')
            if self.media_item.subtitle_file:
                if self.media_item.subtitle_format in ['ass', 'srt', 'sub']:
                    if self.media_item.subtitle_track:
                        v1 = ffmpeg.filter(v1, 'subtitles', self.media_item.subtitle_file, si=self.media_item.subtitle_track)
                    else:
                        v1 = ffmpeg.filter(v1, 'subtitles', self.media_item.subtitle_file)
                elif self.media_item.subtitle_format in ['pgs']:
                    inS = ffmpeg.input(self.media_item.subtitle_file)
                    vS = ffmpeg.filter(inS['s:%s'%self.media_item.subtitle_track if self.media_item.subtitle_track else '0'], 'scale', w=W, h=H)
                    v1 = ffmpeg.overlay(v1, vS)
            if self.media_type == "music":
                v1 = ffmpeg.drawtext(v1, '{}'.format(self.media_item.title),
                                     x=36, y=H - 36 - CLIENT_DRAWTEXT_FONT_SIZE,
                                     escape_text=False,
                                     shadowcolor=CLIENT_DRAWTEXT_SHADOW_COLOR,
                                     shadowx=CLIENT_DRAWTEXT_SHADOW_X,
                                     shadowy=CLIENT_DRAWTEXT_SHADOW_Y,
                                     fontsize=CLIENT_DRAWTEXT_FONT_SIZE,
                                     fontfile=CLIENT_DRAWTEXT_FONT_FILE,
                                     fontcolor=CLIENT_DRAWTEXT_FONT_COLOR,
                                     alpha='if(lt(t,10),0,if(lt(t,11),(t-10)/1,if(lt(t,21),1,if(lt(t,22),(1-(t-21))/1,0))))')
            a1 = in1['a:m:language:eng'] if self.media_item.force_english else in1['a:%s'%self.media_item.audio_track]
            output_stream = ffmpeg.concat(v1, a1, v=1, a=1)

        self.ff = ffmpeg.output(output_stream,
                                'pipe:',
                                vcodec=CLIENT_VCODEC,
                                pix_fmt=PIX_FMT,
                                aspect=CLIENT_ASPECT,
                                flags=CLIENT_FLAGS,
                                g=CLIENT_G,
                                acodec=CLIENT_ACODEC,
                                strict=CLIENT_STRICT,
                                ab=CLIENT_AUDIO_BITRATE,
                                ar=CLIENT_AUDIO_RATE,
                                ac='2',
                                preset=PRESET,
                                format=CLIENT_FORMAT,
                                hls_allow_cache=CLIENT_HLS_ALLOW_CACHE,
                                hls_time=CLIENT_HLS_TIME,
                                hls_list_size=CLIENT_HLS_LIST_SIZE)
        self.cmd = ['ffmpeg', '-re'] + ffmpeg.get_args(self.ff)
        self.process = subprocess.Popen(self.cmd, stdout=self.server.stdin, stderr=None if CLIENT_DEBUG else devnull)

        try:
            timeout = self.media_item.duration / 1000
            flex = CLIENT_FLEX
            self.process.wait(timeout=timeout+flex)
        except subprocess.TimeoutExpired:
            LOGGER.log(TYPE_ERROR, 'Taking longer to play than expected, killing current item')
            self.process.kill()
            self.process.returncode = 0

        return self.process.returncode

    def stop(self):
        self.process.terminate()

# ========================
# Server Class
# ========================
SERVER_DEBUG = True

class Server:
    def __init__(self, output):
        self.ff = ''
        self.process = None
        self.output = output
        self.overlay_file = ffmpeg.input(OVERLAY_FILE, loop=1, t=4)
        if OVERLAY_FILE_OUTLINE:
            self.overlay_file_outline = ffmpeg.input(OVERLAY_FILE_OUTLINE, loop=1, t=4)

    def start(self):
        LOGGER.log(TYPE_INFO, f'Starting Server, output to: {self.output}')
        in1 = ffmpeg.input('pipe:')
        v1 = ffmpeg.drawtext(
            in1['v'], '%{localtime:%R}',
            x=SERV_DRAWTEXT_X, y=SERV_DRAWTEXT_Y,
            escape_text=False,
            shadowcolor=SERV_DRAWTEXT_SHADOW_COLOR,
            shadowx=SERV_DRAWTEXT_SHADOW_X,
            shadowy=SERV_DRAWTEXT_SHADOW_Y,
            fontsize=SERV_DRAWTEXT_FONT_SIZE,
            fontfile=SERV_DRAWTEXT_FONT_FILE,
            fontcolor=SERV_DRAWTEXT_FONT_COLOR)
        v1 = ffmpeg.overlay(v1, self.overlay_file, x=OVERLAY_X, y=OVERLAY_Y)
        if OVERLAY_FILE_OUTLINE:
            v1 = ffmpeg.overlay(v1, self.overlay_file_outline, x=OVERLAY_X, y=OVERLAY_Y)
        a1 = in1['a']
        joined = ffmpeg.concat(v1, a1, v=1, a=1)
        self.ff = ffmpeg.output(joined, self.output,
                                format='hls',
                                hls_time='4',
                                hls_list_size='10',
                                hls_flags='delete_segments+append_list',
                                hls_segment_filename='/dev/shm/hls/stream%d.ts',
                                vcodec=SERV_OUTPUT_VCODEC,
                                pix_fmt=PIX_FMT,
                                aspect=SERV_OUTPUT_ASPECT,
                                crf=SERV_OUTPUT_CRF,
                                tune='zerolatency',
                                acodec=SERV_OUTPUT_ACODEC,
                                preset=PRESET)
        self.cmd = ['ffmpeg'] + ffmpeg.get_args(self.ff)
        self.process = subprocess.Popen(self.cmd, stdin=subprocess.PIPE, stdout=devnull, stderr=None if SERVER_DEBUG else devnull)
        LOGGER.log(TYPE_INFO, 'Server Process Created')
        return self.process

# ========================
# Block & Scheduler
# ========================
class Block:
    def __init__(self, name, folder, num_files, mode, bump_chance, upnext_enabled, subtitles, audio_track):
        self.name = name
        self.folder = folder
        self.num_files = int(num_files)
        self.mode = mode
        self.bump_chance = float(bump_chance)
        self.upnext_enabled = int(upnext_enabled)
        self.subtitles = int(subtitles)
        self.audio_track = int(audio_track) if audio_track else False
        if mode == "music":
            self.playlist = gen_music_playlist(folder, self.num_files)
        else:
            self.playlist = gen_playlist(folder, mode, self.num_files, subtitles=self.subtitles, audio_track=self.audio_track)
        if self.upnext_enabled == 1:
            upnext = gen_upnext(SCHEDULER_UPNEXT_VIDEO_FOLDER, SCHEDULER_UPNEXT_AUDIO_FOLDER, self.name, self.playlist, SCHEDULER_UPNEXT_WISDOM_FILE)
            self.playlist.insert(0, upnext)
        else:
            just_advance_timeindex(self.playlist)

class Scheduler:
    def __init__(self, input_file):
        self.config = configparser.ConfigParser()
        if not os.path.isfile(input_file):
            LOGGER.log(TYPE_INFO, f'Playout file not found!: {input_file}')
        self.config.read(input_file)
        self.blocklist = []
        for sec in self.config.sections():
            try: audio_track = self.config[sec]['override_audio']
            except: audio_track = False
            block = Block(self.config[sec]['name'], self.config[sec]['folder'], self.config[sec]['files'],
                          self.config[sec]['mode'], self.config[sec]['bump_chance'], self.config[sec]['upnext_enabled'],
                          self.config[sec]['subtitles'], audio_track)
            self.blocklist.append(block)

# ========================
# Main Program
# ========================

def init_args():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("-o", "--output", help="output location (stream url)", action="store")
    parser.add_argument("-ua", "--upnext_audio_dir", help="dir for upnext audio files", action="store")
    parser.add_argument("-uv", "--upnext_video_dir", help="dir for upnext video files", action="store")
    parser.add_argument("-uw", "--upnext_wisdom_file", help="file for wisdom text", action="store")
    parser.add_argument("-b", "--bumper_dir", help="dir for bumpers", action="store")
    parser.add_argument("-of", "--overlay_file", help="image overlay on top right", action="store")
    parser.add_argument("-f", "--font_file", help="font file for overlay text", action="store")
    parser.add_argument("-p", "--playout_file", help="playout config file", action="store")
    parser.add_argument("-1", "--once", help="only run through playout once", action="store_true")
    args = vars(parser.parse_args())
    global OUTPUT_LOCATION, SCHEDULER_UPNEXT_VIDEO_FOLDER, SCHEDULER_UPNEXT_AUDIO_FOLDER, SCHEDULER_UPNEXT_WISDOM_FILE, BUMP_FOLDER, OVERLAY_FILE, CLIENT_DRAWTEXT_FONT_FILE, PLAYOUT_FILE, LOOP
    if args['output']: OUTPUT_LOCATION = args['output']
    if args['upnext_audio_dir']: SCHEDULER_UPNEXT_AUDIO_FOLDER = args['upnext_audio_dir']
    if args['upnext_video_dir']: SCHEDULER_UPNEXT_VIDEO_FOLDER = args['upnext_video_dir']
    if args['upnext_wisdom_file']: SCHEDULER_UPNEXT_WISDOM_FILE = args['upnext_wisdom_file']
    if args['bumper_dir']: BUMP_FOLDER = args['bumper_dir']
    if args['overlay_file']: OVERLAY_FILE = args['overlay_file']
    if args['font_file']: CLIENT_DRAWTEXT_FONT_FILE = args['font_file']
    if args['playout_file']: PLAYOUT_FILE = args['playout_file']
    if args['once']: LOOP = False
    return args

def signal_handler(sig, frame):
    LOGGER.log(TYPE_CRIT, f"{sig} received, exiting program!")
    kill_children()
    sys.exit(0)

def kill_children():
    parent = psutil.Process(getpid())
    for child in parent.children(recursive=True):
        print(f"{child.name()} pid:{child.pid} killed!")
        child.kill()

def play_item(item, server):
    retries = 0
    client = Client(item, server)
    while True:
        ret = client.play()
        if ret != 0:
            LOGGER.log(TYPE_ERROR, f"FFMPEG Return Code {ret}, trying again")
            retries += 1
            if retries >= MAX_SAME_FILE_RETRIES:
                LOGGER.log(TYPE_ERROR, "Retry limit reached, giving up!")
                return 1
        else:
            client.stop()
            return 0

def main():
    init_args()
    signal.signal(signal.SIGINT, signal_handler)
    server = Server(OUTPUT_LOCATION).start()
    bumplist = gen_playlist(BUMP_FOLDER)
    consecutive_retries = 0

    while True:
        global TIME_INDEX
        TIME_INDEX = datetime.now()
        scheduler = Scheduler(PLAYOUT_FILE)
        LOGGER.log(TYPE_INFO, f'Scheduler Created, PLAYOUT_FILE: {PLAYOUT_FILE}')
        LOGGER.log(TYPE_INFO, f'Schedule will end at: {TIME_INDEX.strftime("%H:%M")}')
        for block in scheduler.blocklist:
            for x in range(len(block.playlist)):
                ret = play_item(block.playlist[x], server)
                if ret == 0:
                    if len(bumplist) > 0 and block.playlist[x].media_type == "regular" and x < len(block.playlist)-1 and random.SystemRandom().random() > 1-block.bump_chance:
                        LOGGER.log(TYPE_INFO, "Bump chance succeeded, playing bump.")
                        play_item(random.SystemRandom().choice(bumplist), server)
                else:
                    consecutive_retries += 1
                    if consecutive_retries >= MAX_CONSECUTIVE_RETRIES:
                        LOGGER.log(TYPE_CRIT, f"{consecutive_retries} Retries consecutive reached, shutting down!")
                        kill_children()
                        sys.exit(0)
        if not LOOP:
            LOGGER.log(TYPE_INFO, 'Schedule Finished, shutting down.')
            break
        else:
            LOGGER.log(TYPE_INFO, 'Schedule Finished, looping.')
    server.terminate()
    sys.exit(0)

if __name__ == "__main__":
    main()
