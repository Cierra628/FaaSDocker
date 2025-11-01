import requests
import os,time

def main(param):
    file_name = '/proxy/exec/actions/network/' + param['name']
    #上传文件到服务器
    file = {'file': open(file_name,'rb')}
    start_time = time.time()
    r = requests.post('http://172.17.0.1:12345/upload', files=file)
    latency = time.time()-start_time
    print('latency :',latency)
    return{"latency":latency}