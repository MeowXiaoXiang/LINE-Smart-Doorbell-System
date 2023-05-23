#!/bin/bash

echo "這可能要花費些時間，請耐心等待。"

# Upgrade Numpy
pip3 install -U numpy

# Install OpenCV & dependencies
sudo apt-get install libatlas-base-dev libhdf5-dev libhdf5-serial-dev libatlas-base-dev libjasper-dev libqtgui4 libqt4-test
sudo apt-get install -y libopencv-dev python3-opencv

# Install loguru
pip3 install loguru

# Install LINE BOT SDK
pip3 install line-bot-sdk

# Install OLED Package
pip3 install luma.oled

echo "安裝完成！"