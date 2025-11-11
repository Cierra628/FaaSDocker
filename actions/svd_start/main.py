import numpy as np
import scipy.linalg
import json
import os

STORAGE_DIR = '/storage'

def main(event):
    # 从 controller 接收参数
    row_num = int(event.get('row_num', 1000))
    col_num = int(event.get('col_num', 100))
    slice_num = int(event.get('slice_num', 2)) #
    
    # 确保输出目录存在
    output_dir = os.path.join(STORAGE_DIR, 'svd_input')
    os.makedirs(output_dir, exist_ok=True)
    
    print(f"SVD_START: Generating matrix ({row_num}, {col_num}) and splitting into {slice_num} slices.")
    
    # 1. 生成大矩阵
    mat = np.random.rand(row_num, col_num)
    
    # 2. 切片
    mat_list = np.array_split(mat, slice_num) #
    slice_paths = []
    
    # 3. 将切片保存到 /storage
    for i, mat_slice in enumerate(mat_list):
        slice_filename = f'slice_{i}.npy'
        slice_filepath = os.path.join(output_dir, slice_filename)
        
        # 使用 numpy.save 保存
        np.save(slice_filepath, mat_slice)
        slice_paths.append(slice_filepath)
        print(f"SVD_START: Saved {slice_filepath}")

    # 4. 返回包含所有切片*路径*的列表
    return {
        "slice_paths": slice_paths,
        "slice_num": slice_num
    }