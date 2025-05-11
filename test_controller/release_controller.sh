#!/bin/bash

TAG=$1
if [ -z "$TAG" ]; then
  echo "請輸入版本 tag，例如: ./release_controller.sh v1.2.3"
  exit 1
fi

echo "開始打包 image: $TAG"
./package_image.sh $TAG

echo "修改 YAML image tag..."
YAML_PATH="../infra/ml-serving-pod-controller-deployment.yaml"
sed -i "s|\(ml-serving-pod-controller:\).*|\1$TAG|" $YAML_PATH

# echo "Git commit + push..."
# cd ../infra
# git add $YAML_PATH
# git commit -m "Release controller $TAG"
# git push

# echo "完成！Controller $TAG 已部署至 ArgoCD GitOps"
