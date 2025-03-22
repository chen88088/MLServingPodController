import requests
import uuid
from fastapi import FastAPI, HTTPException
from kubernetes import client, config
import os
from fastapi import FastAPI, HTTPException
import redis
import json
from datetime import datetime


app = FastAPI()

# 載入 Kubernetes 配置
config.load_incluster_config()  # 讓 Pod 內可以存取 Kubernetes API
v1 = client.CoreV1Api()

# FASTAPI_SERVICE_URL = "http://fastapi-service.default.svc:80"  # fastapi-server Service


# Redis 用來實現機器 Lock
REDIS_HOST = "localhost"
REDIS_PORT = 6379
redis_lock = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0, decode_responses=True)

CONSUL_HOST = "http://localhost:8500"
SERVICE_NAME = "preprocessing"

@app.get("/health")
def health_check():
    return {"status": "ML Serving is running!"}

# 允許自己接收 API，創建新的 Task Pod
@app.post("/create_new_pod")
def create_new_pod():

     # 讀取自身環境變量中的 PVC 名稱
    pvc_name = os.getenv("PVC_NAME")
    if not pvc_name:
        raise HTTPException(status_code=500, detail="PVC_NAME not set in environment variables")

    pod_name = f"task-pod-{uuid.uuid4().hex[:6]}"
    pod_manifest = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": pod_name, 
            "namespace": "ml-serving",
            "labels": {
                "app": "task-pod",
                "type": "gpu"
            }
        },
        "spec": {
            # "serviceAccountName": "ml-serving-sa",
            "nodeSelector": {
                "gpu-node": "true"
            },
            "containers": [
                {
                    "name": "task-container",
                    "image": "harbor.pdc.tw/moa_ncu/task-pod:latest",
                    "ports": [{"containerPort": 8002}],
                    "volumeMounts": [
                        {
                            "name": "shared-storage",
                            "mountPath": "/mnt/storage"
                        }
                    ],
                    "resources": {
                        "limits": {
                            "nvidia.com/gpu": "1"  # 限制 GPU 使用量
                        }
                    }
                }
            ],
            "volumes": [
                {
                    "name": "shared-storage",
                    "persistentVolumeClaim": {
                        "claimName": pvc_name
                    }
                }
            ]
        }
    }
    v1.create_namespaced_pod(namespace="ml-serving", body=pod_manifest)
    return {"message": "New ml-serving Pod created", "pod_name": pod_name}


@app.post("/allocate_server/{service_name}")
async def allocate_preprocessing_server(dag_id: str, execution_id: str, service_name:str):
    """
    DAG 來請求 Preprocessing Server：
    1. 查詢 Consul 獲取所有可用的機器
    2. 確保機器未被其他 DAG 鎖定
    3. 若可用，則鎖定機器(through resdis)並 回傳 IP、Port、Execution ID
    """
    dag_unique_id = f"{dag_id}_{execution_id}"

    service_name = service_name

    # 1️⃣ 查詢 Consul，獲取所有註冊的 Server
    response = requests.get(f"{CONSUL_HOST}/v1/catalog/service/{service_name}")
    if response.status_code != 200:
        raise HTTPException(status_code=500, detail="❌ 無法從 Consul 獲取可用機器")

    machines = response.json()

    if not machines:
        raise HTTPException(status_code=404, detail="❌ 沒有可用的 Preprocessing Server")

    # 2️⃣ 過濾出未被鎖定的機器
    for machine in machines:
        machine_id = machine["ServiceID"]
        machine_ip = machine["ServiceAddress"]
        machine_port = machine["ServicePort"]

        # 檢查是否已經被其他 DAG 鎖定
        locked_dag = redis_lock.get(f"locked_dag_{machine_id}")
        if locked_dag:
            continue  # 跳過這台機器，因為它已被鎖定

        # 3️⃣ 鎖定該機器 && return sever info
        redis_lock.setex(f"locked_dag_{machine_id}", 3600, dag_unique_id)  # 設定 TTL 1 小時

        return {
            "assigned_machine": machine_id,
            "assigned_ip": machine_ip,
            "assigned_port": machine_port,
            "execution_id": dag_unique_id
        }

    raise HTTPException(status_code=404, detail="所有 Preprocessing Server 皆被鎖定，請稍後重試")


@app.post("/release_server")
async def release_preprocessing_server(dag_id: str, execution_id: str, assigned_machine: str):
    """
    DAG 完成後，釋放  Server
    """
    dag_unique_id = f"{dag_id}_{execution_id}"
    locked_dag = redis_lock.get(f"locked_dag_{assigned_machine}")

    if not locked_dag:
        return {"status": "No Lock Exists", "message": "目前沒有鎖定的機器"}

    if locked_dag != dag_unique_id:
        return {
            "status": "Unauthorized",
            "message": f"機器 {assigned_machine} 目前被 {locked_dag} 鎖定，{dag_unique_id} 無法解除鎖。"
        }

    # 解除鎖定
    redis_lock.delete(f"locked_dag_{assigned_machine}")
    return {"status": "Unlocked", "message": f"機器 {assigned_machine} 已釋放，由 {dag_unique_id} 釋放"}