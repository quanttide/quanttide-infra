#!/usr/bin/env python3
"""算家云一键 CLI —— 容器中心实例的程序化操作。

打通的流程：登录 → 查区域/节点/镜像 → 创建实例 → 等就绪 → 开放端口 → 拿访问地址
→ 关机/开机/释放。接口逆向自算家云容器中心前端（2026-11-27），清单见 README.md。

凭证只走环境变量 SUANJIAYUN_USERNAME / SUANJIAYUN_PASSWORD，会话缓存在
~/.config/suanjiayun/session.json（0600），不落仓库。
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time
from urllib.parse import urlparse

import requests

ORIGIN = "https://suanjiayun.com"
API_ROOT = f"{ORIGIN}/container/api"
SESSION_FILE = pathlib.Path.home() / ".config" / "suanjiayun" / "session.json"

LIFECYCLE_TERMINAL_OK = {"running"}
LIFECYCLE_FAIL = {"buildErr"}


def die(msg: str, code: int = 1):
    print(f"错误: {msg}", file=sys.stderr)
    sys.exit(code)


class Client:
    def __init__(self) -> None:
        self.s = requests.Session()
        self.s.headers.update(
            {
                "Accept-Language": "zh,zh-CN;q=0.9,en;q=0.8",
                "X-Timezone": "Asia/Shanghai",
                "User-Agent": "quanttide-suanjiayun-cli/0.1",
            }
        )
        self.token: str | None = None
        self.user_id: str | None = None
        self.project_id: str | None = None
        self.dept_id: str | None = None
        self._load_session()

    # ---------- session ----------
    def _load_session(self) -> None:
        try:
            d = json.loads(SESSION_FILE.read_text())
            self.token = d.get("token")
            self.user_id = d.get("userId")
            self.project_id = d.get("projectId")
            self.dept_id = d.get("deptId")
        except Exception:
            pass

    def save_session(self) -> None:
        SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
        SESSION_FILE.write_text(
            json.dumps(
                {
                    "token": self.token,
                    "userId": self.user_id,
                    "projectId": self.project_id,
                    "deptId": self.dept_id,
                },
                ensure_ascii=False,
            )
        )
        os.chmod(SESSION_FILE, 0o600)

    # ---------- http ----------
    @staticmethod
    def _url(path: str, area_api: str | None = None) -> str:
        path = path.lstrip("/")
        if area_api:
            # 区域 API 是独立主机独立端口（如 https://xn-a.suanjiayun.com:4333），
            # 前端调用形如 apiUrl + "/container/api/..."。
            return area_api.rstrip("/") + "/container/api/" + path
        return API_ROOT + "/" + path

    def _auth_headers(self) -> dict:
        if not self.token:
            die("未登录。先 export SUANJIAYUN_USERNAME / SUANJIAYUN_PASSWORD 后执行 `login`")
        return {"Authorization": self.token}

    def read(self, path: str, filt: dict | None = None, area_api: str | None = None, auth: bool = True):
        """Loopback 风格读取：GET ?filter=json，失败回落 POST {filter}。"""
        headers = self._auth_headers() if auth else {}
        params = {"filter": json.dumps(filt, ensure_ascii=False)} if filt else None
        r = self.s.get(self._url(path, area_api), params=params, headers=headers, timeout=30)
        if filt and r.status_code in (400, 404, 405, 415, 500):
            r = self.s.post(self._url(path, area_api), json={"filter": filt}, headers=headers, timeout=30)
        return self._unwrap(r)

    def act(self, path: str, body=None, area_api: str | None = None, method: str = "POST"):
        headers = self._auth_headers()
        r = self.s.request(method, self._url(path, area_api), json=body, headers=headers, timeout=60)
        return self._unwrap(r)

    @staticmethod
    def _unwrap(r: requests.Response):
        if r.status_code == 401:
            try:
                msg = r.json().get("error", {}).get("message", r.text)
            except Exception:
                msg = r.text
            die(f"鉴权失败（401）: {msg}。token 失效就重新 `login`")
        if r.status_code >= 400:
            try:
                msg = r.json().get("error", {}).get("message", r.text)
            except Exception:
                msg = r.text
            die(f"HTTP {r.status_code}: {msg}")
        if not r.text:
            return None
        try:
            return r.json()
        except Exception:
            return r.text

    # ---------- auth ----------
    def login(self) -> None:
        u = os.environ.get("SUANJIAYUN_USERNAME", "").strip()
        p = os.environ.get("SUANJIAYUN_PASSWORD", "").strip()
        if not u or not p:
            die("缺少环境变量 SUANJIAYUN_USERNAME / SUANJIAYUN_PASSWORD")
        r = self.s.post(
            API_ROOT + "/users/login",
            json={"username": u, "password": p},
            headers={"X-Timezone": "Asia/Shanghai"},
            timeout=30,
        )
        if r.status_code != 200:
            try:
                msg = r.json().get("error", {}).get("message", r.text)
            except Exception:
                msg = r.text
            die(f"登录失败 HTTP {r.status_code}: {msg}")
        d = r.json()
        self.token = d.get("id")
        self.user_id = d.get("userId")
        if not self.token:
            die(f"登录响应里没有 token: {d}")
        self.resolve_project()
        self.save_session()
        print(f"登录成功 userId={self.user_id} project={self.project_id}")

    @staticmethod
    def _find_projects(obj):
        out = []
        if isinstance(obj, dict):
            v = obj.get("projects")
            if isinstance(v, list):
                out += [p for p in v if isinstance(p, dict) and p.get("id")]
            for x in obj.values():
                out += Client._find_projects(x)
        elif isinstance(obj, list):
            for x in obj:
                out += Client._find_projects(x)
        seen, uniq = set(), []
        for p in out:
            if p["id"] not in seen:
                seen.add(p["id"])
                uniq.append(p)
        return uniq

    def resolve_project(self) -> None:
        """从 roleInfo 里挑项目（default 优先），带出 deptId。"""
        info = self.read(f"users/{self.user_id}/roleInfo")
        projects = self._find_projects(info)
        if not projects:
            die(f"roleInfo 里找不到项目，原始响应: {json.dumps(info, ensure_ascii=False)[:500]}")
        proj = next((p for p in projects if p.get("default") in (1, True)), projects[0])
        self.project_id = proj["id"]
        dept = proj.get("dept") if isinstance(proj.get("dept"), dict) else {}
        self.dept_id = dept.get("id") or proj.get("deptId")
        if not self.dept_id:
            die(f"项目 {self.project_id} 没有 deptId，原始: {json.dumps(proj, ensure_ascii=False)[:400]}")

    def need_project(self) -> None:
        if not self.project_id or not self.dept_id:
            self.resolve_project()
            self.save_session()


# ---------- 资源解析 ----------

def _list_nodes(c: Client, version_type: int, gpu_type: int) -> list:
    r = c.s.get(
        API_ROOT + "/con_areas/list-nodes",
        params={"versionType": version_type, "gpuType": gpu_type, "globalOp": "neq"},
        timeout=30,
    )
    return c._unwrap(r)


def pick_area(areas: list, key: str) -> dict:
    key_l = key.lower()
    for a in areas:
        if a.get("id") == key or key_l in str(a.get("name", "")).lower():
            return a
    die(f"没找到区域 {key}，可选: {', '.join(a.get('name', '?') for a in areas)}")


def pick_device(area: dict, key: str) -> dict:
    key_l = key.lower()
    for d in area.get("con_deviceTypes", []):
        if key_l in str(d.get("name", "")).lower() or key_l == str(d.get("weight", "")).lower():
            return d
    die(f"区域 {area.get('name')} 下没找到 GPU {key}，可选: "
        f"{', '.join(d.get('name', '?') for d in area.get('con_deviceTypes', []))}")


def fetch_node_rows(c: Client, area: dict, device: dict, version_type: int, charge: str, count: int) -> list:
    filt = {
        "where": {
            "areas": [area["id"]],
            "gpuTypes": [device["id"]],
            "chargeMode": charge,
            "count": count,
            "versionType": version_type,
            "global": {"neq": 1},
            "or": [{"shareResource": 1}],
        },
        "limit": 50,
    }
    rows = c.read("con_nodes/realTime", filt=filt, auth=False)
    return rows or []


def pick_node(rows: list, node_id: str | None, count: int) -> dict:
    if node_id:
        for r in rows:
            if r.get("nodeId") == node_id or r.get("name") == node_id:
                return r
        die(f"没找到节点 {node_id}")
    free = [r for r in rows if ((r.get("gpuSum") or 0) - (r.get("gpuUsed") or 0)) >= count]
    if not free:
        die("该区域/卡型下没有空闲 GPU 节点，换个区域或卡型试试")
    return sorted(free, key=lambda r: r.get("amountPrice") or 1e9)[0]


def fetch_mirrors(c: Client, query: str | None) -> list:
    filt = {"where": {"state": 1}, "order": ["usage DESC", "date_created DESC"], "limit": 200}
    rows = c.read("communityMirrors", filt=filt, auth=False) or []
    if query:
        q = query.lower()
        rows = [m for m in rows if q in str(m.get("mirrorName", "")).lower()]
    return rows


def fetch_base_images(c: Client) -> list:
    filt = {
        "where": {"state": 1, "type": "base", "lifecycle": "ready"},
        "order": "date_created DESC",
        "limit": 200,
    }
    return c.read("con_images", filt=filt, auth=False) or []


def resolve_image(c: Client, args) -> dict:
    """返回 {imageId, mirrorId|None, name}。--mirror 优先（社区镜像，H3 走这条）。"""
    if getattr(args, "mirror", None):
        mirrors = fetch_mirrors(c, None)
        q = args.mirror.lower()
        hit = next((m for m in mirrors if m.get("id") == args.mirror), None)
        if not hit:
            hit = next((m for m in mirrors if q in str(m.get("mirrorName", "")).lower()), None)
        if not hit:
            die(f"没找到镜像 {args.mirror}，用 `images --q {args.mirror}` 搜")
        image_id = hit.get("imageId")
        if not image_id:
            detail = c.read(f"communityMirrors/{hit['id']}", auth=False)
            image_id = (detail or {}).get("imageId")
        if not image_id:
            die(f"镜像 {hit.get('mirrorName')} 没有 imageId")
        return {"imageId": image_id, "mirrorId": hit["id"], "name": hit.get("mirrorName", "")}
    if getattr(args, "image", None):
        return {"imageId": args.image, "mirrorId": None, "name": args.image}
    die("需要 --mirror <镜像名或id> 或 --image <con_images id>")


def instance_row(c: Client, instance_id: str) -> dict:
    c.need_project()
    rows = c.read(f"projects/{c.project_id}/instances/own", filt={"where": {"id": instance_id}})
    if not rows:
        die(f"找不到实例 {instance_id}（已释放的实例查不到，用 `list --all` 看历史）")
    return rows[0]


def area_api_of(row: dict) -> str | None:
    con_area = row.get("con_area") if isinstance(row.get("con_area"), dict) else {}
    return con_area.get("APIUrl") or (row.get("area") or {}).get("APIUrl")


# ---------- 命令 ----------

def cmd_areas(c: Client, args) -> None:
    areas = _list_nodes(c, args.version, 2)
    for a in areas:
        gpus = ", ".join(
            f"{d.get('name')}({next((p.get('value') for p in d.get('parameter', []) if p.get('key') == 'xc'), '?')})"
            for d in a.get("con_deviceTypes", [])
        )
        print(f"{a.get('name')}  id={a['id']}  api={a.get('APIUrl')}")
        print(f"    GPU: {gpus}")


def cmd_nodes(c: Client, args) -> None:
    areas = _list_nodes(c, args.version, 2)
    area = pick_area(areas, args.area)
    device = pick_device(area, args.gpu)
    rows = fetch_node_rows(c, area, device, args.version, "amount", 1)
    if not rows:
        die(f"{area['name']} / {device['name']} 没有可用节点（realTime 返回空）")
    print(f"{area['name']} · {device['name']} · {'专业版' if args.version == 0 else '青春版'}")
    print(f"{'节点':<10} {'空闲/总卡':>10} {'单价':>10} {'CUDA':>8} {'磁盘余量':>10}  nodeId")
    for r in sorted(rows, key=lambda x: x.get("amountPrice") or 1e9):
        free = (r.get("gpuSum") or 0) - (r.get("gpuUsed") or 0)
        disk_free = (r.get("diskSum") or 0) - (r.get("diskUsed") or 0)
        price = r.get("price") or {}
        unit = str(price.get("unit") or "小时")
        unit = unit if unit.startswith("/") else "/" + unit
        print(
            f"{r.get('name', '?'):<10} {free:>4}/{r.get('gpuSum', 0):<4} "
            f"{price.get('value', r.get('amountPrice')):>7}元{unit:<6} "
            f"{str(r.get('cuda', '-')):>8} {disk_free:>7}MB  {r.get('nodeId')}"
        )


def cmd_images(c: Client, args) -> None:
    print("== 社区镜像（镜像社区）")
    for m in fetch_mirrors(c, args.q):
        print(f"  {m.get('mirrorName')}  [{m.get('imageType', '?')}]  id={m.get('id')}")
    if not args.base:
        return
    print("== 基础镜像")
    for i in fetch_base_images(c):
        name = f"{i.get('name')}/{i.get('tag')}"
        if args.q and args.q.lower() not in name.lower():
            continue
        print(f"  {name}  id={i.get('id')}")


def cmd_login(c: Client, args) -> None:
    c.login()


def cmd_whoami(c: Client, args) -> None:
    if not c.token:
        die("未登录")
    info = c.read("users/me/info")
    c.need_project()
    c.save_session()
    name = info.get("username") or info.get("name") or c.user_id
    print(f"user={name} userId={c.user_id}\nproject={c.project_id} dept={c.dept_id}")
    count = c.read("con_instances/my-count")
    print(f"实例数={count.get('count') if isinstance(count, dict) else count}")


def cmd_list(c: Client, args) -> None:
    c.need_project()
    filt = {"order": "date_created DESC", "limit": 50}
    if args.all:
        filt["where"] = {}
    else:
        filt["where"] = {"lifecycle": {"nin": ["released"]}}
    rows = c.read(f"projects/{c.project_id}/instances/own", filt=filt) or []
    if not rows:
        print("（没有实例）")
        return
    print(f"{'实例':<26} {'状态':<12} {'区域':<8} {'端口':>6}  id")
    for r in rows:
        con_area = r.get("con_area") or {}
        area_name = (con_area.get("name") or r.get("areaId") or "?")
        print(
            f"{str(r.get('alias') or r.get('name') or '-')[:24]:<26} "
            f"{str(r.get('lifecycle', '?')):<12} {str(area_name)[:8]:<8} "
            f"{str(r.get('targetPort') or '-'):>6}  {r.get('id')}"
        )


def cmd_create(c: Client, args) -> str | None:
    c.need_project()
    # 首次创建有行为验证码闸门
    cnt = c.read("con_instances/my-count")
    count_val = cnt.get("count") if isinstance(cnt, dict) else 0
    if not count_val and not args.force:
        die(
            "账号还没有实例，首次创建要求过人机验证码，CLI 绕不开。"
            "请先在网页上手动创建一台（任意配置即可，创建后立即释放也行），"
            "之后的创建就全部走 CLI。确认要带验证码信息继续可加 --force（仍会失败，仅供抓包）。",
            code=2,
        )

    areas = _list_nodes(c, args.version, 2)
    area = pick_area(areas, args.area)
    device = pick_device(area, args.gpu)
    rows = fetch_node_rows(c, area, device, args.version, args.charge, args.count)
    row = pick_node(rows, args.node, args.count)

    img = resolve_image(c, args)
    payload = {
        "buildImage": {"mode": "base", "value": "", "imageId": img["imageId"]},
        "instanceTypeId": row["instanceTypeId"],
        "nodeId": row["nodeId"],
        "versionType": row.get("versionType", args.version),
        "gpuType": row.get("gpuType", device),
        "count": args.count,
        "payObj": {
            "timeLength": 1,
            "price": row.get("price", {}),
            "priceDisk": row.get("priceDisk", {}),
            "chargeMode": args.charge,
        },
        "area": row.get("area", area),
        "dataDiskExtend": args.disk,
        "youngEndTime": "",
        "userId": c.user_id,
        "deptId": c.dept_id,
    }
    if img["mirrorId"]:
        payload["chooseImageType"] = "communityMirrors"
        payload["communityMirrorId"] = img["mirrorId"]

    if args.dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    area_api = (row.get("area") or {}).get("APIUrl")
    resp = c.act(f"projects/{c.project_id}/instances", payload, area_api=area_api)
    iid = resp.get("id") if isinstance(resp, dict) else None
    if not iid:
        die(f"创建响应异常: {json.dumps(resp, ensure_ascii=False)[:400]}")
    price = row.get("price") or {}
    print(f"创建成功 instance={iid}")
    print(f"  镜像={img['name']} 节点={row.get('name')} 单价={price.get('value')}{price.get('unit', '')}/卡")
    print(f"  下一步: suanjiayun.py wait {iid} && suanjiayun.py port {iid}  （或直接 `up` 一键跑完）")
    return iid


def _wait_running(c: Client, iid: str, timeout: int) -> dict:
    deadline = time.time() + timeout
    last = "?"
    while time.time() < deadline:
        row = instance_row(c, iid)
        last = row.get("lifecycle", "?")
        if last in LIFECYCLE_TERMINAL_OK:
            return row
        if last in LIFECYCLE_FAIL:
            die(f"实例创建失败 lifecycle={last}")
        print(f"  ... {last}")
        time.sleep(10)
    die(f"等待 running 超时（{timeout}s），最后状态 {last}。实例还在，稍后 `list` 查看")


def cmd_wait(c: Client, args) -> None:
    row = _wait_running(c, args.instance, args.timeout)
    print(f"就绪: {row.get('id')} running")


def cmd_start(c: Client, args) -> None:
    row = instance_row(c, args.instance)
    if row.get("lifecycle") == "running":
        print("已在运行")
        return
    c.act(
        f"projects/{c.project_id}/instances/{args.instance}/startUp/{args.mode}",
        {"id": args.instance, "deptId": c.dept_id, "language": "zh"},
        area_api=area_api_of(row),
    )
    print(f"已提交开机（{args.mode}），`wait {args.instance}` 等就绪")


def cmd_stop(c: Client, args) -> None:
    row = instance_row(c, args.instance)
    if row.get("lifecycle") != "running":
        print(f"当前状态 {row.get('lifecycle')}，无需关机")
        return
    c.act(
        f"projects/{c.project_id}/instances/{args.instance}/shutDown",
        {"id": args.instance},
        area_api=area_api_of(row),
    )
    print("已提交关机（计费停止，7 天后会自动释放，数据提前备份）")


def cmd_release(c: Client, args) -> None:
    row = instance_row(c, args.instance)
    c.act(
        f"projects/{c.project_id}/instances/{args.instance}/release",
        args.instance,  # 前端这里传的就是裸 id
        area_api=area_api_of(row),
    )
    print("已提交释放（不可逆：系统盘/数据盘一并释放）")


def cmd_port(c: Client, args) -> None:
    row = instance_row(c, args.instance)
    area_api = area_api_of(row)
    action = "closePort" if args.close else "openPort"
    resp = c.act(
        f"projects/{c.project_id}/instances/{args.instance}/{action}",
        {"id": args.instance},
        area_api=area_api,
    )
    if args.close:
        print("端口已关闭")
        return
    port = resp.get("port") if isinstance(resp, dict) else None
    host = urlparse(area_api).hostname if area_api else None
    print(f"端口已开放: {port}")
    if port and host:
        print(f"访问地址: http://{host}:{port}")


def cmd_up(c: Client, args) -> None:
    """一键：解析节点+镜像 → 创建 → 等就绪 → 开端口 → 打印地址。"""
    ns = argparse.Namespace(
        area=args.area, gpu=args.gpu, version=args.version, charge=args.charge,
        count=args.count, disk=args.disk, node=args.node,
        mirror=args.mirror, image=args.image, dry_run=False, force=args.force,
    )
    iid = cmd_create(c, ns)
    if not iid:
        die("创建未返回实例 id")
    print(f"[1/3] 等待实例就绪: {iid}")
    _wait_running(c, iid, args.timeout)
    print("[2/3] 开放端口")
    row = instance_row(c, iid)
    area_api = area_api_of(row)
    resp = c.act(
        f"projects/{c.project_id}/instances/{iid}/openPort",
        {"id": iid},
        area_api=area_api,
    )
    port = resp.get("port") if isinstance(resp, dict) else None
    host = urlparse(area_api).hostname if area_api else None
    print("[3/3] 完成")
    if port and host:
        print(f"访问地址: http://{host}:{port}")
    else:
        print(f"端口={port}（地址主机名取区域 API 主机 {host}，若打不开用网页端确认）")
    print(f"实例 id={iid}，收工记得 down（关机停钱）")


def cmd_down(c: Client, args) -> None:
    row = instance_row(c, args.instance)
    area_api = area_api_of(row)
    if args.release:
        c.act(f"projects/{c.project_id}/instances/{args.instance}/release", args.instance, area_api=area_api)
        print("已释放（不可逆）")
        return
    if row.get("targetPort"):
        try:
            c.act(
                f"projects/{c.project_id}/instances/{args.instance}/closePort",
                {"id": args.instance},
                area_api=area_api,
            )
            print("端口已关")
        except SystemExit:
            print("（关端口失败，忽略）")
    if row.get("lifecycle") == "running":
        c.act(
            f"projects/{c.project_id}/instances/{args.instance}/shutDown",
            {"id": args.instance},
            area_api=area_api,
        )
        print("已关机（计费停止）")
    else:
        print(f"状态 {row.get('lifecycle')}，未在运行")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="suanjiayun", description="算家云容器中心一键 CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, **kwargs):
        sp = sub.add_parser(name, **kwargs)
        sp.set_defaults(fn=fn)
        return sp

    add("areas", cmd_areas, help="列出可用区与 GPU 型号（公开）").add_argument(
        "--version", type=int, default=0, choices=[0, 1], help="0=专业版 1=青春版"
    )

    sp = add("nodes", cmd_nodes, help="列节点与实时单价（公开）")
    sp.add_argument("--area", required=True, help="区域名或 id，如 西南A区")
    sp.add_argument("--gpu", required=True, help="GPU 型号子串，如 4090 / 5090")
    sp.add_argument("--version", type=int, default=0, choices=[0, 1])

    sp = add("images", cmd_images, help="搜镜像（社区镜像 + 可选基础镜像）")
    sp.add_argument("--q", help="关键词，如 MiniMax-H3")
    sp.add_argument("--base", action="store_true", help="同时列基础镜像")

    add("login", cmd_login, help="登录（读 SUANJIAYUN_USERNAME/PASSWORD）")
    add("whoami", cmd_whoami, help="当前身份、项目与实例数")

    sp = add("list", cmd_list, help="列实例")
    sp.add_argument("--all", action="store_true", help="含已释放")

    sp = add("create", cmd_create, help="创建实例")
    sp.add_argument("--area", required=True)
    sp.add_argument("--gpu", required=True)
    sp.add_argument("--mirror", help="社区镜像名子串或 id（如 MiniMax-H3）")
    sp.add_argument("--image", help="con_images 基础镜像 id")
    sp.add_argument("--node", help="指定 nodeId（默认挑有空闲的最便宜节点）")
    sp.add_argument("--count", type=int, default=1, help="GPU 卡数")
    sp.add_argument("--charge", default="amount", choices=["amount", "day", "week", "month"])
    sp.add_argument("--disk", type=int, default=0, help="数据盘扩容 GB")
    sp.add_argument("--version", type=int, default=0, choices=[0, 1])
    sp.add_argument("--dry-run", action="store_true", help="只打印 payload")
    sp.add_argument("--force", action="store_true", help="跳过首创建验证码闸门检查")

    sp = add("wait", cmd_wait, help="等实例 running")
    sp.add_argument("instance")
    sp.add_argument("--timeout", type=int, default=900)

    sp = add("start", cmd_start, help="开机")
    sp.add_argument("instance")
    sp.add_argument("--mode", default="gpu", choices=["gpu", "nogpu"], help="nogpu=无卡开机 0.01 元/时")

    add("stop", cmd_stop, help="关机（停计费）").add_argument("instance")

    sp = add("release", cmd_release, help="释放实例（不可逆）")
    sp.add_argument("instance")

    sp = add("port", cmd_port, help="开放端口并打印访问地址")
    sp.add_argument("instance")
    sp.add_argument("--close", action="store_true", help="关端口")

    sp = add("up", cmd_up, help="一键：创建→就绪→开端口→地址")
    sp.add_argument("--area", required=True)
    sp.add_argument("--gpu", required=True)
    sp.add_argument("--mirror", help="社区镜像名子串或 id")
    sp.add_argument("--image", help="基础镜像 id")
    sp.add_argument("--node")
    sp.add_argument("--count", type=int, default=1)
    sp.add_argument("--charge", default="amount", choices=["amount", "day", "week", "month"])
    sp.add_argument("--disk", type=int, default=0)
    sp.add_argument("--version", type=int, default=0, choices=[0, 1])
    sp.add_argument("--timeout", type=int, default=900)
    sp.add_argument("--force", action="store_true")

    sp = add("down", cmd_down, help="关端口+关机（--release 则释放）")
    sp.add_argument("instance")
    sp.add_argument("--release", action="store_true", help="释放实例（不可逆）")

    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    c = Client()
    args.fn(c, args)


if __name__ == "__main__":
    main()
