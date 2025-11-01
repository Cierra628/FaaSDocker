import os #用于拼接文件路径
import time #计时工具
from flask import Flask, request #flask是python的一个web框架；request用来获取用户请求中发来的数据
from gevent.pywsgi import WSGIServer #高性能web服务器，让flask应用可以同时处理很多请求
from multiprocessing import Process

exec_path = '/proxy/exec/actions' #告诉程序用户的Action代码在哪里
default_file = 'main.py' #规定每个Action文件夹内的入口文件名必须是main.py

class ActionRunner: #一个蓝图，一个工厂，用于创建执行器对象
    def __init__(self): #创建runner对象时自动执行的一个构造函数
        self.code = None
        self.action = None
        self.action_context = None

    def init(self, inp): #代码加载方法（与前者不是一个东西），对应init接口，负责将main.py读入内存并编译，参数inp存储用户发来的输入字典
        action = inp['action']

        # update action status
        self.action = action

        # compile the python file first
        filename = os.path.join(exec_path, action + '/' + default_file)
        with open(filename, 'r') as f:#with 语句的作用是确保文件在代码块执行完毕后，无论是否发生错误，都会被自动关闭
            code = compile(f.read(), filename, mode='exec')

        self.action_context = {} #清空上下文，创建一个干净的字典，用于存储 matmul Action 的所有代码元素？？？
        exec(code, self.action_context) #核心： 运行 matmul/main.py 中的所有顶级代码（import numpy、def main 等）。运行结束后，self.action_context 字典中就有了 main 函数和 np

        return True

    def run(self, inp): #代码运行方法，对应run接口
        self.action_context['data'] = inp #将输入数据 inp 存储到上下文字典中，并命名为 data。这样 main 函数就可以通过 data 访问输入？？？

        out = eval('main(data)', self.action_context) #核心中的核心： 运行代码 main(data)。Python 在 self.action_context 中找到 main 函数和 data 变量，并调用 main({"param": 1000})。这行代码开始执行您的矩阵乘法。 矩阵乘法的结果（{"latency": 0.xxx}）被存储到 out 变量中。
        return out

#Flask应用配置
#由于它不在任何函数或类内部，它在文件被加载和解析到这个位置时，立即就被执行了。
#这段代码的作用是：在服务器正式启动（即 server.serve_forever() 运行）之前，先创建好所有核心对象（proxy 和 runner），并设置好它们的初始状态和配置，确保服务处于“可接收请求”的准备状态。
proxy = Flask(__name__) #创建一个 Flask 应用程序实例，并命名为 proxy
proxy.status = 'new' #设置服务的初始状态为 'new'（新启动）
proxy.debug = False #关闭调试模式，让服务运行更安全。
runner = ActionRunner() #实例化（创建）我们上面解释的那个核心执行对象。

#状态接口
@proxy.route('/status', methods=['GET']) #设定：当收到 HTTP GET 请求访问 /status 这个网址时，运行下面的 status 函数。
def status():
    res = {}
    res['status'] = proxy.status #返回服务的当前状态（'new'、'init' 或 'ok'）。
    res['workdir'] = os.getcwd() #返回程序当前的工作目录。
    if runner.action:
        res['action'] = runner.action
    return res #将状态信息（JSON 格式）返回给用户？？？

#初始化接口
@proxy.route('/init', methods=['POST']) #设定：当收到 HTTP POST 请求访问 /init 时，运行下面的 init 函数。
def init():
    proxy.status = 'init' #临时更新服务状态为 'init'（正在初始化）。

    inp = request.get_json(force=True, silent=True) #获取用户通过 POST 请求发送过来的 JSON 数据（如{"action": "matmul"}）
    runner.init(inp) #调用上面解释的 ActionRunner.init 方法，执行文件加载和编译

    proxy.status = 'ok' #初始化完成后，将服务状态设置为 'ok'（准备就绪）。
    return ('OK', 200) #返回 OK 文本和标准的成功状态码。


#运行接口
@proxy.route('/run', methods=['POST']) #设定：当收到 HTTP POST 请求访问 /run 时，运行下面的 run 函数。
def run():
    proxy.status = 'run'
    
    inp = request.get_json(force=True, silent=True)
    # record the execution time
    start = time.time() #记录开始计时。

    runner.run(inp)
    '''
    process_ = Process(target=runner.run, args=[inp])
    process_.start()
    process_.join(timeout=max(0, inp['timeout'] - 0.005))
    process_.terminate()
    '''

    # out = runner.run(inp)
    end = time.time() #记录结束计时。
    print('duration:', end - start)
    data = {
        "start_time": start,
        "end_time": end,
        "duration": end - start,
        # "result": out
    }

    proxy.status = 'ok'
    return data

if __name__ == '__main__': #这是一个通用的 Python 约定。它确保只有当您直接执行 python3 proxy.py 时，它里面的代码才会运行。如果文件是被其他程序导入的，这段代码就不会运行。这避免了当其他程序仅仅是想导入 proxy.py 中的某些函数时，服务器却意外启动的情况。
    server = WSGIServer(('0.0.0.0', 5000), proxy) #1. WSGIServer 是一个高性能的服务器（来自 gevent 库）。2. ('0.0.0.0', 5000) 指定了服务器监听的网络地址和端口。0.0.0.0 表示监听所有网络接口（即允许外部访问），5000 是端口号？？？。3. proxy 是我们之前定义的 Flask 应用程序实例。这一行就是告诉服务器：“请使用这个 Flask 应用来处理所有传入到 5000 端口的请求。”
    server.serve_forever() #这是一个阻塞（Blocking）函数。一旦运行，程序就会一直保持活动状态，不断地等待、接收和响应来自网络（例如您的 curl 命令）的 HTTP 请求，直到您手动停止容器（docker stop）。
