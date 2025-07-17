import os
import sys
import re
import subprocess
from collections import defaultdict

# --- 配置 ---
# 从环境变量中读取目标仓库信息
TARGET_REGISTRY = os.getenv("TARGET_REGISTRY")
TARGET_NAMESPACE = os.getenv("TARGET_NAMESPACE")
ISSUE_BODY = sys.argv[1] if len(sys.argv) > 1 else ""

def log(message):
    """打印日志信息"""
    print(f"INFO: {message}", flush=True)

def run_command(command):
    """执行 shell 命令并实时打印输出"""
    log(f"Executing command: {' '.join(command)}")
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8')
    for line in iter(process.stdout.readline, ''):
        print(line.strip(), flush=True)
    process.wait()
    if process.returncode != 0:
        raise subprocess.CalledProcessError(process.returncode, command)

def parse_issue(body):
    """
    解析 Issue Body，根据架构分组镜像。
    返回一个字典，键是架构字符串（如 'amd64,arm64'），值是镜像列表。
    """
    lines = body.strip().splitlines()
    if not lines:
        raise ValueError("Issue body is empty.")

    arch_groups = defaultdict(list)
    global_arch_str = ""
    source_images_for_comment = []

    # 1. 解析全局 ARCH
    first_line = lines[0].strip()
    if first_line.upper().startswith("ARCH:"):
        global_arch_str = first_line[5:].strip()
        log(f"Found global ARCH directive: {global_arch_str}")
        image_lines = lines[1:]
    else:
        image_lines = lines

    # 2. 解析每一行镜像
    image_pattern = re.compile(r"^(.*?)(?:\s*--arch\s+(.*))?$", re.IGNORECASE)
    for line in image_lines:
        line = line.strip()
        if not line:
            continue
        
        match = image_pattern.match(line)
        if not match:
            log(f"Skipping invalid line: {line}")
            continue

        image_name = match.group(1).strip()
        line_arch_str = match.group(2)
        
        # 移除可能存在的斜杠或冒号
        if image_name.endswith('/') or image_name.endswith(':'):
            image_name = image_name[:-1]

        if not image_name:
            continue
        
        # 记录原始镜像名用于最终评论
        source_images_for_comment.append(image_name)

        # 决定最终架构
        # 优先级: 行内 --arch > 全局 ARCH: > "all" (表示同步所有架构)
        effective_arch = "all"
        if line_arch_str:
            effective_arch = line_arch_str.strip()
            log(f"Image '{image_name}' uses inline arch: {effective_arch}")
        elif global_arch_str:
            effective_arch = global_arch_str
            log(f"Image '{image_name}' uses global arch: {effective_arch}")
        else:
            log(f"Image '{image_name}' has no arch specified, will sync all architectures.")

        arch_groups[effective_arch].append(image_name)

    return arch_groups, source_images_for_comment

def main():
    if not all([TARGET_REGISTRY, TARGET_NAMESPACE]):
        raise ValueError("Environment variables TARGET_REGISTRY and TARGET_NAMESPACE must be set.")

    try:
        arch_groups, source_images = parse_issue(ISSUE_BODY)
    except ValueError as e:
        print(f"ERROR: Invalid issue format. {e}", file=sys.stderr)
        sys.exit(1) # 以错误码退出，使 workflow step 失败

    if not arch_groups:
        log("No valid images found to sync.")
        return

    all_target_images = []
    
    # 为每个架构组执行同步
    for arch, images in arch_groups.items():
        log("-" * 50)
        log(f"Processing group for arch: '{arch}'")
        
        # 1. 生成 image-syncer 配置文件
        sync_config_content = ""
        for source_image in images:
            # 从源镜像路径中提取最后一部分作为目标镜像名
            tag_name = source_image.split('/')[-1]
            target_image = f"{TARGET_REGISTRY}/{TARGET_NAMESPACE}/{tag_name}"
            sync_config_content += f"{source_image}: {target_image}\n"
            all_target_images.append(target_image)
            
        with open("images.yml", "w") as f:
            f.write(sync_config_content)
        
        log("Generated images.yml for this group:")
        print(sync_config_content, flush=True)

        # 2. 构建并执行 image-syncer 命令
        command = ["./image-syncer", "--auth=./auth.yml", "--images=./images.yml", "--proc=10"]
        if arch.lower() != "all":
            command.extend(["--arch", arch])
        
        try:
            run_command(command)
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            print(f"ERROR: Syncing failed for arch '{arch}'. Reason: {e}", file=sys.stderr)
            sys.exit(1) # 同步失败则立即退出

    # 3. 创建用于最终评论的文件
    with open("dockerhub-image.yml", "w") as f:
        f.write("\n".join(all_target_images))
    
    # 将原始的、不带 ARCH 的镜像列表写入文件
    with open("images-init.yml", "w") as f:
        f.write("\n".join(source_images))

    log("=" * 50)
    log("All sync tasks completed successfully.")

if __name__ == "__main__":
    main()
