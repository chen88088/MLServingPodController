from fastapi import FastAPI, HTTPException
import os
from kubernetes import client, config
import uuid
import time

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
@app.post("/create_pod/{ml_serving_pod_server_image_name}")
def create_pod(ml_serving_pod_server_image_name: str, image_tag:str,export_port:int):
    
    

    # 確認 Image Name 格式
    if not ml_serving_pod_server_image_name:
        raise HTTPException(status_code=400, detail="Image Name is required.")
    if "/" in ml_serving_pod_server_image_name or ":" in ml_serving_pod_server_image_name:
        raise HTTPException(status_code=400, detail="Invalid Image Name.")
    
    export_port = export_port
    image_tag = image_tag
    
    # 拼接 Image 完整名稱
    full_image_name = f"harbor.pdc.tw/moa_ncu/{ml_serving_pod_server_image_name}:{image_tag}"

    print("🚀 Image used for deployment:", full_image_name)

    pod_name = f"ml-serving-{ml_serving_pod_server_image_name}-{uuid.uuid4().hex[:6]}"  # 生成隨機 Pod 名稱
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
                    "image": full_image_name,
                    "imagePullPolicy": "Always",
                    "ports": [{"containerPort": export_port}],  # 這裡假設 ML Server 跑在 export_port
                     "env": [  # 傳遞 PVC 名稱，讓 ml-serving Pod 知道要共用的 PVC
                        {
                            "name": "PVC_NAME",
                            "value": pvc_name
                        },
                        {
                            "name": "GITHUB_TOKEN",
                            "valueFrom": {
                                "secretKeyRef": {
                                    "name": "github-token",
                                    "key": "GITHUB_TOKEN"
                                }
                            }
                        },
                        {"name": "MLFLOW_TRACKING_URI", "valueFrom": {"configMapKeyRef": {"name": "mlflow-config", "key": "MLFLOW_TRACKING_URI"}}},
                        {"name": "MLFLOW_S3_ENDPOINT_URL", "valueFrom": {"configMapKeyRef": {"name": "mlflow-config", "key": "MLFLOW_S3_ENDPOINT_URL"}}},
                        {"name": "AWS_ACCESS_KEY_ID", "valueFrom": {"secretKeyRef": {"name": "mlflow-secret", "key": "AWS_ACCESS_KEY_ID"}}},
                        {"name": "AWS_SECRET_ACCESS_KEY", "valueFrom": {"secretKeyRef": {"name": "mlflow-secret", "key": "AWS_SECRET_ACCESS_KEY"}}}
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
            ],
            # 加入這行，指定 Image Pull Secret
            "imagePullSecrets": [
                {
                    "name": "harbor-secret"  # 與 harbor-secret.yaml 中的 name 相同
                }
            ]
        }
    }
    v1.create_namespaced_pod(namespace="ml-serving", body=pod_manifest)

    # 等待 Pod 啟動，最多嘗試 30 次 (約 60 秒)
    pod_ip = None
    for _ in range(30):
        pod_info = v1.read_namespaced_pod(name=pod_name, namespace="ml-serving")
        if pod_info.status.phase == "Running":
            pod_ip = pod_info.status.pod_ip
            break
        time.sleep(2)  # 每次等待 2 秒

    if not pod_ip:
        raise HTTPException(status_code=500, detail="Pod did not reach Running state in time.")

    return {
        "message": "Pod created",
        "pod_name": pod_name,
        "image": full_image_name,
        "pod_ip": pod_ip
    }


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