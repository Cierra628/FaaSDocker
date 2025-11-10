import subprocess
import logging
import os
import math

MAX_SPLIT_NUM = 4 #
LOGGER = logging.getLogger()
STORAGE_DIR = '/storage' # 我们共享的卷目录

def getVideoDuration(input_video):
    #
    cmd = f'ffprobe -i {input_video} -show_entries format=duration -v quiet -of csv="p=0"'
    raw_result = subprocess.check_output(cmd, shell=True) #
    return float(raw_result.decode().strip()) #

def exec_FFmpeg_cmd(cmd_lst):
    try:
        subprocess.run(cmd_lst, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, shell=True) #
    except subprocess.CalledProcessError as exc:
        LOGGER.error(f'FFmpeg Error: {exc.stderr}') #
        raise

# proxy.py 将调用这个函数
def main(event):
    video_name = event['video_name']
    segment_time_seconds = int(event['segment_time'])

    input_filepath = os.path.join(STORAGE_DIR, video_name)
    
    # 在共享卷上创建输出目录
    video_proc_dir = os.path.join(STORAGE_DIR, 'split_output')
    os.makedirs(video_proc_dir, exist_ok=True) #

    video_duration = getVideoDuration(input_filepath) #
    split_num = math.ceil(video_duration / segment_time_seconds) #
    
    if split_num > MAX_SPLIT_NUM: #
        segment_time_seconds = int(math.ceil(video_duration / MAX_SPLIT_NUM)) + 1 #

    shortname, extension = os.path.splitext(video_name) #
    
    # FFmpeg 直接写入共享卷
    command = (
        f'ffmpeg -i {input_filepath} -c copy -f segment ' #
        f'-segment_time {segment_time_seconds} -reset_timestamps 1 ' #
        f'{video_proc_dir}/split_{shortname}_piece_%02d{extension}' #
    )
    exec_FFmpeg_cmd([command]) #

    # 收集在共享卷上创建的文件名
    split_keys = []
    for filename in os.listdir(video_proc_dir): #
        if filename.startswith(f'split_{shortname}'): #
            # 返回完整路径以供下一步使用
            split_keys.append(os.path.join(video_proc_dir, filename)) #

    # 将文件列表返回给编排器
    return {'split_keys': split_keys}