import subprocess
import logging
import os
import time

LOGGER = logging.getLogger()
STORAGE_DIR = '/storage' # 我们共享的卷目录

def exec_FFmpeg_cmd(cmd_lst):
    try:
        subprocess.run(cmd_lst, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, shell=True) #
    except subprocess.CalledProcessError as exc:
        LOGGER.error(f'FFmpeg Error: {exc.stderr}') #
        raise

def get_fileNameExt(filename):
    (fileDir, tempfilename) = os.path.split(filename) #
    (shortname, extension) = os.path.splitext(tempfilename) #
    return fileDir, tempfilename, shortname, extension #

# proxy.py 将调用这个函数
def main(event):
    # 从 split 步骤接收完整的文件路径
    # 注意：在您的原始 main(tramscode).py 中，您从 'video' 键获取数据
    # 我们需要统一输入。假设编排器将发送 'split_file'。
    input_filepath = event['split_file'] 
    target_type = event['target_type'] #

    # 在共享卷上创建转码输出目录
    transcoded_output_dir = os.path.join(STORAGE_DIR, 'transcode_output')
    os.makedirs(transcoded_output_dir, exist_ok=True)

    # 生成唯一的输出文件名
    fileDir, tempfilename, shortname, extension = get_fileNameExt(input_filepath)
    transcoded_filename = f'transcoded_{shortname}.{target_type}' #
    transcoded_filepath = os.path.join(transcoded_output_dir, transcoded_filename)

    # FFmpeg 从共享卷读取并写入共享卷
    exec_FFmpeg_cmd([
        f'ffmpeg -y -threads 1 -i {input_filepath} -threads 1 {transcoded_filepath}' #
    ])

    # 返回新的转码文件路径
    return {'transcoded_file': transcoded_filepath}