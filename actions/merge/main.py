import subprocess
import logging
import os

LOGGER = logging.getLogger()
STORAGE_DIR = '/storage' # 我们共享的卷目录

def get_fileNameExt(filename):
    (fileDir, tempfilename) = os.path.split(filename) #
    (shortname, extension) = os.path.splitext(tempfilename) #
    return fileDir, tempfilename, shortname, extension #

# proxy.py 将调用这个函数
def main(event):
    # 接收转码后的文件路径列表
    transcoded_files = event['transcoded_files']
    target_type = event['target_type'] #
    output_prefix = event['output_prefix'] #
    video_name = event['video_name'] # 原始视频名称

    # 在共享卷上创建最终输出目录
    merge_output_dir = os.path.join(STORAGE_DIR, 'merge_output')
    os.makedirs(merge_output_dir, exist_ok=True)

    fileDir1, filename1, shortname1, extension1 = get_fileNameExt(video_name) #
    
    # 片段列表文件写入共享卷
    segs_filename = f'segs_{shortname1}_{target_type}.txt' #
    segs_filepath = os.path.join(merge_output_dir, segs_filename) #

    # 写入文件的 *确切* 路径 (它们已经在 /storage 中)
    with open(segs_filepath, 'w') as f: #
        for filepath in sorted(transcoded_files): # 确保顺序
            f.write(f"file '{filepath}'\n") #

    merged_filename = f'{output_prefix}_{shortname1}.{target_type}' #
    merged_filepath = os.path.join(merge_output_dir, merged_filename) #

    # FFmpeg 从共享卷读取片段列表和文件
    # 并将最终输出写入共享卷
    os.system(
        f'ffmpeg -f concat -safe 0 -i {segs_filepath} -c copy -fflags +genpts {merged_filepath}' #
    )

    LOGGER.info(f"最终合并文件位于: {merged_filepath}") #
    
    # 返回最终文件的路径
    return {'final_video': merged_filepath}