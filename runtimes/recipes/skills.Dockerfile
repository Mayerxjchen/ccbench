# Skill bundle 镜像 —— 只承载 benchmark skills,不作为任务执行镜像。
#
# 用途:With-Skill 条件时,eval 从本镜像一次性提取 /opt/electromind/skills
#       到宿主 jobs/<run>/skills_src/,作为 skill_roots 喂给 SkillRegistry。
#       任务本身永远跑原始计算镜像(compute image),容器里没有此路径,
#       杜绝 agent 绕过 install_skills 直接读文件。
#
# 构建:
#   cd runtimes/recipes && bash build.sh skills
FROM ubuntu:24.04

COPY skills/ /opt/electromind/skills/
