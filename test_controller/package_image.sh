#!/bin/bash

# 允許傳入版本號
TAG=${1:-latest}

IMAGE_NAME=ml-serving-pod-controller
REGISTRY=harbor.pdc.tw/moa_ncu
FULL_IMAGE=$REGISTRY/$IMAGE_NAME:$TAG

echo "Start packaging Docker image (version: $TAG)..."

# Step 1: Build image
docker build -t $IMAGE_NAME .

# Step 2: 取得 image ID
IMAGE_ID=$(docker images $IMAGE_NAME:latest -q)

# Step 3: Tag image
docker tag $IMAGE_ID $FULL_IMAGE

# Step 4: Push image
docker push $FULL_IMAGE

echo "✅ Done! Image pushed: $FULL_IMAGE"
