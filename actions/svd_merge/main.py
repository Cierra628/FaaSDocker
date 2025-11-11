import numpy as np
import scipy.linalg
import json
import os

STORAGE_DIR = '/storage'

def main(event):
    # 从 controller 接收所有 compute 任务的结果
    # event['results'] 应该是一个列表:
    # [ {"mat_index": 0, "u_path": "...", "s_path": "..."},
    #   {"mat_index": 1, "u_path": "...", "s_path": "..."} ]
    results = event.get('results', [])
    
    if not results:
        raise ValueError("SVD_MERGE: No results to merge.")
        
    print(f"SVD_MERGE: Merging {len(results)} partial results...")

    # 1. 排序并从 /storage 加载所有 u 和 s
    results.sort(key=lambda x: x['mat_index'])
    
    u_list = [np.load(r['u_path']) for r in results]
    s_list = [np.load(r['s_path']) for r in results]
    
    # 2. 执行合并逻辑
    U = np.hstack(u_list) #
    S = np.diag(np.hstack(s_list)) #
    
    # 3. 执行最终的 SVD
    u_final, s_final, v_final = np.linalg.svd(S, full_matrices=False) #
    
    U_final = np.dot(U, u_final)
    
    # 4. 确保最终输出目录存在
    output_dir = os.path.join(STORAGE_DIR, 'svd_final')
    os.makedirs(output_dir, exist_ok=True)
    
    # 5. 将最终结果保存到 /storage
    final_u_path = os.path.join(output_dir, 'final_U.npy')
    final_s_path = os.path.join(output_dir, 'final_S.npy')
    final_v_path = os.path.join(output_dir, 'final_V.npy')
    
    np.save(final_u_path, U_final)
    np.save(final_s_path, s_final)
    np.save(final_v_path, v_final)

    print(f"SVD_MERGE: Merge complete. Final results saved.")

    # 6. 返回最终结果的路径
    return {
        "final_u_path": final_u_path,
        "final_s_path": final_s_path,
        "final_v_path": final_v_path
    }