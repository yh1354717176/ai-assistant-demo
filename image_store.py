import streamlit as st
import threading

# 🖼️ 图片存储（解决线程隔离与 Streamlit Rerun 状态丢失问题）
# 使用 @st.cache_resource 确保对象在不同 Rerun 间保持同一个实例
@st.cache_resource
class ImageStore:
    def __init__(self):
        self.images = []
        self.lock = threading.Lock()
    
    def add(self, img_data):
        with self.lock:
            self.images.append(img_data)
            
    def get_and_clear(self):
        with self.lock:
            imgs = list(self.images)
            self.images.clear()
            return imgs

@st.cache_resource
def get_image_store():
    return ImageStore()
