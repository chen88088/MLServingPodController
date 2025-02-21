from fastapi import FastAPI, HTTPException
import os
from kubernetes import client, config
import uuid

def load_k8s_config():
    """ 自動偵測 Kubernetes 環境，選擇合適的 config """
    try:
        if "KUBERNETES_SERVICE_HOST" in os.environ:
            # Pod 內部環境：使用 InCluster Config
            config.load_incluster_config()
            print("✅ Running inside Kubernetes: Using InCluster Config")
        else:
            # 本機開發環境：使用 Kube Config
            config.load_kube_config()
            print("⚠️ Running locally: Using Kube Config")
    except config.ConfigException as e:
        print(f"❌ Failed to load Kubernetes config: {e}")

# 讀取 Kubernetes 設定
load_k8s_config()

# Kubernetes API 物件
v1 = client.CoreV1Api()
app = FastAPI()

@app.get("/health")
def health_check():
    return {"status": "CONTROLLER SERVER is running!!!!!"}

# 創建 Pod
@app.post("/create_pod")
def create_pod():
    pod_name = f"ml-serving-{uuid.uuid4().hex[:6]}"  # 生成隨機 Pod 名稱
    pod_manifest = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": pod_name, "labels": {"app": "ml-serving"}},
        "spec": {
            "serviceAccountName": "ml-serving-sa",
            "containers": [
                {
                    "name": "ml-serving-container",
                    "image": "192.168.158.43:80/library/ml-serving-pod:latest",
                    "ports": [{"containerPort": 30001}],  # 這裡假設 ML Server 跑在 30001Port
                }
            ]
        }
    }
    v1.create_namespaced_pod(namespace="default", body=pod_manifest)
    return {"message": "Pod created", "pod_name": pod_name}

# 刪除 Pod
@app.delete("/delete_pod/{pod_name}")
def delete_pod(pod_name: str):
    try:
        v1.delete_namespaced_pod(name=pod_name, namespace="default")
        return {"message": "Pod deleted", "pod_name": pod_name}
    except client.exceptions.ApiException as e:
        raise HTTPException(status_code=400, detail=f"Failed to delete pod: {e}")

# 查詢目前所有 ML Serving Pod
@app.get("/list_pods")
def list_pods():
    pods = v1.list_namespaced_pod(namespace="default", label_selector="app=ml-serving")
    pod_names = [pod.metadata.name for pod in pods.items]
    return {"pods": pod_names}