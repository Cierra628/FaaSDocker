import numpy as np
import scipy.linalg
import json
import os

STORAGE_DIR = '/storage'

def main(event):
    # 从 controller 接收*一个*切片路径
    slice_path = event.get('slice_path')
    mat_index = int(event.get('mat_index')) # 索引 (0, 1, ...)
    
    if not slice_path or not os.path.exists(slice_path):
        raise FileNotFoundError(f"SVD_COMPUTE: Slice file not found at {slice_path}")

    print(f"SVD_COMPUTE: Loading slice {mat_index} from {slice_path}")
    
    # 1. 从 /storage 加载矩阵
    mat_slice = np.load(slice_path)
    
    # 2. 执行 SVD 计算
    u, s, v = np.linalg.svd(mat_slice, full_matrices=False) #
    
    # 3. 确保输出目录存在
    output_dir = os.path.join(STORAGE_DIR, 'svd_output')
    os.makedirs(output_dir, exist_ok=True)
    
    # 4. 将 u, s, v 结果保存到 /storage
    u_path = os.path.join(output_dir, f'u_{mat_index}.npy')
    s_path = os.path.join(output_dir, f's_{mat_index}.npy')
    v_path = os.path.join(output_dir, f'v_{mat_index}.npy')
    
    np.save(u_path, u)
    np.save(s_path, s)
    np.save(v_path, v)
    
    print(f"SVD_COMPUTE: Finished slice {mat_index}. Results saved.")

    # 5. 返回指向*结果路径*的 JSON
    return {
        "mat_index": mat_index,
        "u_path": u_path,
        "s_path": s_path,
        "v_path": v_path
    }