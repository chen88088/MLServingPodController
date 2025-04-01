from fastapi import FastAPI, HTTPException
import os
from kubernetes import client, config
import uuid
import time
import redis
import json
from datetime import datetime
import requests
from pydantic import BaseModel
import re
import hashlib
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


def sanitize_k8s_name(name: str) -> str:
    # 將 `/`, `_` 轉成 `-`，再移除其他不合法字元
    name = name.replace("/", "-").replace("_", "-")
    name = re.sub(r"[^a-z0-9-]+", "", name.lower())
    name = re.sub(r"-+", "-", name).strip("-")  # 避免多個 dash 或首尾 dash
    return name

def generate_safe_name(base_name: str, prefix: str, suffix: str = "", rand_len: int = 6, max_len: int = 63):
    """
    安全產生 pod/service name，避免超過 63 字元
    base_name: 來源名稱（如 image）
    prefix/suffix: 前綴/後綴字串
    """
    base = base_name.split("/")[-1].replace("_", "-").replace(".", "-")
    rand_str = uuid.uuid4().hex[:rand_len]
    hash_part = hashlib.sha1(base.encode()).hexdigest()[:6]

    # 預估長度
    fixed_len = len(prefix) + len(suffix) + len(rand_str) + len(hash_part) + 4  # 多 4 個 "-"
    max_base_len = max_len - fixed_len
    short_base = base[:max_base_len]

    return f"{prefix}-{short_base}-{hash_part}-{rand_str}{('-' + suffix) if suffix else ''}"

app = FastAPI()

# 讀取 Kubernetes 設定
load_k8s_config()
# Kubernetes API 物件
v1 = client.CoreV1Api()


# Redis 用來實現機器 Lock
REDIS_HOST = "redis.redis.svc.cluster.local"
REDIS_PORT = 6379
redis_lock = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0, decode_responses=True)

CONSUL_HOST = "http://consul-consul-server.consul.svc.cluster.local:8500"


class PodCreateRequest(BaseModel):
    image_name: str
    image_tag: str
    export_port: int

class AllocateExternalServiceRequest(BaseModel):
    dag_id: str
    execution_id: str

@app.get("/health")
def health_check():
    return {"status": "CONTROLLER SERVER is running!!!!!"}

# 創建 Pod
@app.post("/create_pod")
def create_pod(request: PodCreateRequest):
    
    image_name= request.image_name
    image_tag = request.image_tag
    export_port = request.export_port

    # 確認 Image Name 格式
    if not image_name:
        raise HTTPException(status_code=400, detail="Image Name is required.")
    if ":" in image_name:
        raise HTTPException(status_code=400, detail="Invalid Image Name.")
    
    # 拼接 Image 完整名稱
    full_image_name = f"harbor.pdc.tw/{image_name}:{image_tag}"

    print("Image used for deployment:", full_image_name)

    # 替換掉不合法的字元
    ml_serving_pod_name = sanitize_k8s_name(image_name)

    # 生成合法的 Pod 名稱
    pod_name = f"ml-serving-{ml_serving_pod_name}-{uuid.uuid4().hex[:6]}"  # 生成隨機 Pod 名稱
    
    # 安全生成 pod 名稱
    pod_name = generate_safe_name(image_name, prefix="mlpod")

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
                "app": pod_name  
            }
        },
        "spec": {
            "serviceAccountName": "ml-serving-sa",
            "containers": [
                {
                    "name": "ml-serving-container",
                    "image": full_image_name,
                    "imagePullPolicy": "Always",
                    "ports": [{"name": "http", "containerPort": export_port}],  # 這裡假設 ML Server 跑在 export_port
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
    print("==== Pod Manifest ====")
    print(json.dumps(pod_manifest, indent=2))
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
    
    # 3.  create svc
    service_name = generate_safe_name(image_name, prefix="mlsvc")
    service_manifest = {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {
            "name": service_name,
            "namespace": "ml-serving"
        },
        "spec": {
            "selector": {
                "app": pod_name  
            },
            "ports": [
                {
                    "protocol": "TCP",
                    "port": export_port,
                    "targetPort": "http"
                }
            ]
        }
    }
    print("==== Service Manifest ====")
    print(json.dumps(service_manifest, indent=2))

    # 確保 containerPorts fully visible
    for _ in range(10):
        pod_desc = v1.read_namespaced_pod(name=pod_name, namespace="ml-serving")
        ports = pod_desc.spec.containers[0].ports
        if ports and ports[0].container_port == export_port:
            break
        time.sleep(2)
    else:
        raise HTTPException(status_code=500, detail="Pod container port not ready for service binding.")

    v1.create_namespaced_service(namespace="ml-serving", body=service_manifest)

    return {
        "message": "Pod created",
        "pod_name": pod_name,
        "image": full_image_name,
        "pod_ip": pod_ip,
        "pod_service": f"{service_name}.ml-serving.svc.cluster.local"
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

        # 刪除 Service（如果有的話）
        service_name = f"{pod_name}-svc"
        try:
            v1.delete_namespaced_service(name=service_name, namespace="ml-serving")
        except client.exceptions.ApiException as e:
            # 如果找不到 service 就略過，但紀錄 log
            if e.status != 404:
                raise
        
        return {"message": "Pod ,SVC and PVC deleted", "pod_name": pod_name, "pvc_name": pvc_name, "service_name": service_name}
    except client.exceptions.ApiException as e:
        raise HTTPException(status_code=400, detail=f"Failed to delete pod or pvc: {e}")


# 查詢目前所有 ML Serving Pod
@app.get("/list_pods")
def list_pods():
    pods = v1.list_namespaced_pod(namespace="default", label_selector="app=ml-serving")
    pod_names = [pod.metadata.name for pod in pods.items]
    return {"pods": pod_names}

##############################################################

# Allocate External Service
@app.post("/allocate_service/{service_name}")
async def allocate__service(service_name:str, request: AllocateExternalServiceRequest):
    """
    DAG 來請求 Server：
    1. 查詢 Consul 獲取所有可用的機器
    2. 確保機器未被其他 DAG 鎖定
    3. 若可用，則鎖定機器(through resdis)並 回傳 IP、Port、Execution ID
    """
    
    
    dag_id = request.dag_id
    execution_id = request.execution_id

    # 確認 Image Name 格式
    if not (dag_id and execution_id ):
        raise HTTPException(status_code=400, detail="Dag id nd Execution_id required.")

    dag_unique_id = f"{dag_id}_{execution_id}"

    service_name = service_name

    # 1️⃣ 查詢 Consul，獲取所有註冊的 Server
    response = requests.get(f"{CONSUL_HOST}/v1/catalog/service/{service_name}")
    if response.status_code != 200:
        raise HTTPException(status_code=500, detail="❌ 無法從 Consul 獲取可用機器")

    service_instances = response.json()

    if not service_instances:
        raise HTTPException(status_code=404, detail="❌ 沒有可用的 Service_Instance")

    # 2️⃣ 過濾出未被鎖定的機器
    for instance in service_instances:
        service_instance_id = instance["ServiceID"]
        service_instance_ip = instance["ServiceAddress"]
        service_instance_port = instance["ServicePort"]

        # 檢查是否已經被其他 DAG 鎖定
        locked_dag = redis_lock.get(f"locked_dag_{service_instance_id}")
        if locked_dag:
            continue  # 跳過這台機器，因為它已被鎖定

        # 3️⃣ 鎖定該機器 && return sever info
        redis_lock.setex(f"locked_dag_{service_instance_id}", 3600, dag_unique_id)  # 設定 TTL 1 小時

        return {
            "assigned_service_instance_id": service_instance_id,
            "assigned_service_instance_ip": service_instance_ip,
            "assigned_service_instance_port": service_instance_port,
            "For_Dag": dag_unique_id
        }

    raise HTTPException(status_code=404, detail="所有 Preprocessing Server 皆被鎖定，請稍後重試")

# Release External Service
@app.post("/release_service/{assigned_service_instance_id}")
async def release_service(assigned_service_instance_id: str, request: AllocateExternalServiceRequest):
    """
    DAG 完成後，釋放  Server
    """
    dag_id = request.dag_id
    execution_id = request.execution_id

    # 確認 Image Name 格式
    if not (dag_id and execution_id ):
        raise HTTPException(status_code=400, detail="Dag id nd Execution_id required.")

    dag_unique_id = f"{dag_id}_{execution_id}"
    locked_dag = redis_lock.get(f"locked_dag_{assigned_service_instance_id}")

    if not locked_dag:
        return {"status": "No Lock Exists", "message": "目前沒有鎖定的機器"}

    if locked_dag != dag_unique_id:
        return {
            "status": "Unauthorized",
            "message": f"Service Instance : {assigned_service_instance_id} 目前被 {locked_dag} 鎖定，{dag_unique_id} 無法解除鎖。"
        }

    # 解除鎖定
    redis_lock.delete(f"locked_dag_{assigned_service_instance_id}")
    return {"status": "Unlocked", "message": f"Service Instance : {assigned_service_instance_id} 已釋放，由 {dag_unique_id} 釋放"}