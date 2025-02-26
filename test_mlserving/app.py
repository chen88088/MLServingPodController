import requests
import uuid
from fastapi import FastAPI, HTTPException
from kubernetes import client, config
import os

app = FastAPI()

# 載入 Kubernetes 配置
config.load_incluster_config()  # 讓 Pod 內可以存取 Kubernetes API
v1 = client.CoreV1Api()

FASTAPI_SERVICE_URL = "http://fastapi-service.default.svc:80"  # fastapi-server Service

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