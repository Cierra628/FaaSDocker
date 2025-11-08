# controller.py
from flask import Flask, request, jsonify
import threading
from function_manager import FunctionManager
import atexit
import time

app = Flask(__name__)

function_managers = {}
manager_lock = threading.Lock()

@app.route('/create_manager', methods=['POST'])
def create_manager():
    """
    JSON body parameters:
      - function_name: str
      - image_name: str
      - container_port: int (default 5000)
      - host_port_start: int (optional)
      - idle_timeout: int (seconds, optional)
      - min_idle_containers: int (optional)
      - max_containers: int (optional)
    """
    body = request.get_json(silent=True) or {}
    function_name = body.get("function_name")
    if not function_name:
        return jsonify({"error": "function_name required"}), 400

    with manager_lock:
        if function_name in function_managers:
            return jsonify({"status": "exists", "message": f"Manager {function_name} already exists."}), 200

        image_name = body.get("image_name", "myimage:latest")
        container_port = int(body.get("container_port", 5000))
        host_port_start = int(body.get("host_port_start", 8000))
        idle_timeout = int(body.get("idle_timeout", 300))
        min_idle = int(body.get("min_idle_containers", 0))
        max_containers = body.get("max_containers", None)
        if max_containers is not None:
            max_containers = int(max_containers)

        manager = FunctionManager(
            function_name=function_name,
            image_name=image_name,
            container_port=container_port,
            host_port_start=host_port_start,
            idle_timeout=idle_timeout,
            min_idle_containers=min_idle
        )
        function_managers[function_name] = manager
        return jsonify({"status": "created", "function": function_name}), 201

@app.route('/dispatch/<function_name>', methods=['POST'])
def dispatch(function_name):
    """
    Dispatch a user request to a function manager.
    The body is forwarded as JSON to container /run.
    """
    with manager_lock:
        if function_name not in function_managers:
            return jsonify({"error": "unknown function"}), 404
        manager = function_managers[function_name]

    payload = request.get_json(silent=True) or {}
    host_port, container_id = manager.get_container_for_request()
    if not host_port:
        return jsonify({"status": "error", "message": "Failed to get container"}), 500

    try:
        # call /init before /run if needed; here we only forward /run (controller/test can call /init separately)
        # but to mimic previous behavior, call init first with action=function_name
        try:
            init_data = {"action": function_name}
            manager_url = f"http://127.0.0.1:{host_port}"
            print(f"[dispatch] calling {manager_url}/init for {container_id[:12]}")
            # ignore response body for init; non-200 will be caught below
            import requests
            requests.post(f"{manager_url}/init", json=init_data, timeout=10)
        except Exception as e:
            # log, but continue to attempt /run; errors will propagate on /run if fatal
            print(f"[dispatch] init error for container {container_id[:12]}: {e}")

        print(f"[dispatch] forwarding run to http://127.0.0.1:{host_port}/run")
        import requests
        r = requests.post(f"http://127.0.0.1:{host_port}/run", json=payload, timeout=60)
        try:
            data = r.json()
        except Exception:
            data = {"raw": r.text}
        data["status"] = "success"
        data["container"] = container_id[:12]
        status_code = r.status_code
    except Exception as e:
        print(f"[dispatch] error invoking container {container_id[:12]}: {e}")
        data = {"status": "error", "message": str(e)}
        status_code = 502
    finally:
        manager.release_container(container_id)

    return jsonify(data), status_code

@app.route('/manager_status/<function_name>', methods=['GET'])
def manager_status(function_name):
    with manager_lock:
        if function_name not in function_managers:
            return jsonify({"error": "unknown function"}), 404
        m = function_managers[function_name]

    with m.lock:
        total = len(m.containers)
        idle = sum(1 for d in m.containers.values() if d["status"] == "idle")
        busy = sum(1 for d in m.containers.values() if d["status"] == "busy")
        ports = [ {"id": cid[:12], "host_port": d.get("host_port")} for cid,d in m.containers.items() ]
    return jsonify({"function": function_name, "total": total, "idle": idle, "busy": busy, "containers": ports})

# Global cleanup on exit
def clean_up_all_containers_on_exit():
    print("Application exiting. Stopping all function containers...")
    with manager_lock:
        for manager in function_managers.values():
            try:
                manager.stop_all_containers()
            except Exception as e:
                print("Error cleaning manager:", e)
    print("All containers stopped on exit.")

atexit.register(clean_up_all_containers_on_exit)

if __name__ == '__main__':
    # Run flask app directly. Start it in foreground and run tests separately.
    app.run(host='0.0.0.0', port=5000, threaded=True)
