import requests
import uuid
from fastapi import FastAPI
from kubernetes import client, config

app = FastAPI()

# 載入 Kubernetes 配置
config.load_incluster_config()  # 讓 Pod 內可以存取 Kubernetes API
v1 = client.CoreV1Api()

FASTAPI_SERVICE_URL = "http://fastapi-service.default.svc:80"  # fastapi-server Service

@app.get("/health")
def health_check():
    return {"status": "Task Pod is running!"}

# # 允許自己接收 API，創建新的 ml-serving Pod
# @app.post("/create_new_pod")S
# def create_new_pod():
#     pod_name = f"ml-serving-{uuid.uuid4().hex[:6]}"
#     pod_manifest = {
#         "apiVersion": "v1",
#         "kind": "Pod",
#         "metadata": {"name": pod_name, "labels": {"app": "ml-serving"}},
#         "spec": {
#             "containers": [
#                 {
#                     "name": "ml-serving-container",
#                     "image": "192.168.158.43:80/library/ml-serving:latest",
#                     "ports": [{"containerPort": 8001}]
#                 }
#             ]
#         }
#     }
#     v1.create_namespaced_pod(namespace="default", body=pod_manifest)
#     return {"message": "New ml-serving Pod created", "pod_name": pod_name}