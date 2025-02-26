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
    # 1. 動態生成 PVC
    pvc_name = f"{pod_name}-pvc"
    pvc_manifest = {
        "apiVersion": "v1",
        "kind": "PersistentVolumeClaim",
        "metadata": {
            "name": pvc_name,
            "namespace": "ml-serving"
        },
        "spec": {
            "accessModes": ["ReadWriteMany"],
            "storageClassName": "nfs-storage",
            "resources": {
                "requests": {
                    "storage": "5Gi"
                }
            }
        }
    }

    v1.create_namespaced_persistent_volume_claim(namespace="ml-serving", body=pvc_manifest)

    # 2. 生成 Pod
    pod_manifest = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": pod_name, 
            "namespace": "ml-serving",
            "labels": {
                "app": "ml-serving"
            }
        },
        "spec": {
            "serviceAccountName": "ml-serving-sa",
            "containers": [
                {
                    "name": "ml-serving-container",
                    "image": "harbor.pdc.tw/moa_ncu/ml-serving-pod:latest",
                    "ports": [{"containerPort": 8001}],  # 這裡假設 ML Server 跑在 8001Port
                     "env": [  # 傳遞 PVC 名稱，讓 ml-serving Pod 知道要共用的 PVC
                        {
                            "name": "PVC_NAME",
                            "value": pvc_name
                        }
                    ],
                    "volumeMounts": [
                        {
                            "name": "ml-storage",
                            "mountPath": "/mnt/storage"
                        }
                    ]
                }
            ],
            "volumes": [
                {
                    "name": "ml-storage",
                    "persistentVolumeClaim": {
                        "claimName": pvc_name
                    }
                }
            ]
        }
    }
    v1.create_namespaced_pod(namespace="ml-serving", body=pod_manifest)
    return {"message": "Pod created", "pod_name": pod_name}

# 刪除 Pod
@app.delete("/delete_pod/{pod_name}")
def delete_pod(pod_name: str):
    try:
        # 刪除 Pod
        v1.delete_namespaced_pod(name=pod_name, namespace="ml-serving")
        
        # 查詢對應的 PVC
        pvc_name = f"{pod_name}-pvc"
        
        # 刪除 PVC（自動刪除對應 PV）
        v1.delete_namespaced_persistent_volume_claim(name=pvc_name, namespace="ml-serving")
        
        return {"message": "Pod and PVC deleted", "pod_name": pod_name, "pvc_name": pvc_name}
    except client.exceptions.ApiException as e:
        raise HTTPException(status_code=400, detail=f"Failed to delete pod or pvc: {e}")


# 查詢目前所有 ML Serving Pod
@app.get("/list_pods")
def list_pods():
    pods = v1.list_namespaced_pod(namespace="default", label_selector="app=ml-serving")
    pod_names = [pod.metadata.name for pod in pods.items]
    return {"pods": pod_names}