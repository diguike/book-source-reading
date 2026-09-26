# mini_sse.py：最小 SSE 累积器，只认 Anthropic 协议的文本块
import json
import copy

class MiniSSEReassembler:
    def __init__(self):
        self._buf = b""
        self._cur_event = None
        self._cur_data = []
        self.events = []
        self.snapshot = None

    def feed_bytes(self, chunk: bytes):
        self._buf += chunk
        while b"\n" in self._buf:
            line, self._buf = self._buf.split(b"\n", 1)
            self._feed_line(line.decode("utf-8", errors="replace").rstrip("\r"))

    def _feed_line(self, line):
        if line.startswith("event:"):
            self._cur_event = line[6:].strip()
            self._cur_data = []
        elif line.startswith("data:"):
            self._cur_data.append(line[5:].strip())
        elif line == "":
            if self._cur_event or self._cur_data:
                raw = "\n".join(self._cur_data)
                if raw == "[DONE]":             # OpenAI 的结束哨兵，不是数据
                    self._cur_event, self._cur_data = None, []
                    return
                try:
                    data = json.loads(raw)
                except Exception:
                    data = raw
                event_type = self._cur_event or "message"
                self.events.append({"event": event_type, "data": data})
                self._accumulate(event_type, data)
                self._cur_event = None
                self._cur_data = []

    def _accumulate(self, event_type, data):
        # Anthropic 协议 minimal subset
        if event_type == "message_start":
            self.snapshot = copy.deepcopy(data.get("message", {}))
        elif self.snapshot is None:
            return
        elif event_type == "content_block_start":
            block = copy.deepcopy(data.get("content_block", {}))
            content = self.snapshot.setdefault("content", [])
            idx = data.get("index", len(content))
            while len(content) <= idx:
                content.append({})
            content[idx] = block
        elif event_type == "content_block_delta":
            idx = data.get("index", 0)
            delta = data.get("delta", {})
            block = self.snapshot["content"][idx]
            if delta.get("type") == "text_delta":
                block["text"] = block.get("text", "") + delta.get("text", "")
        elif event_type == "message_delta":
            self.snapshot.update(data.get("delta", {}))      # stop_reason 等
            if "usage" in data:
                self.snapshot["usage"] = data["usage"]
        # 工具参数（input_json_delta）、thinking 块和 OpenAI 协议留给读者补


if __name__ == "__main__":
    # 自测：把一段 Anthropic SSE 切成 7 字节的碎片喂进去，结果应与整段喂入相同
    stream = (
        b'event: message_start\ndata: {"message": {"id": "msg_1", "role": "assistant", "content": []}}\n\n'
        b'event: content_block_start\ndata: {"index": 0, "content_block": {"type": "text", "text": ""}}\n\n'
        b'event: content_block_delta\ndata: {"index": 0, "delta": {"type": "text_delta", "text": "\xe4\xbd\xa0\xe5\xa5\xbd"}}\n\n'
        b'event: content_block_delta\ndata: {"index": 0, "delta": {"type": "text_delta", "text": ", world"}}\n\n'
        b'event: message_delta\ndata: {"delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 4}}\n\n'
        b'data: [DONE]\n\n'
        b'data: {"late": true}\n\n'
    )
    r = MiniSSEReassembler()
    for i in range(0, len(stream), 7):
        r.feed_bytes(stream[i:i + 7])
    print(json.dumps(r.snapshot, ensure_ascii=False))
    assert r.snapshot["content"][0]["text"] == "你好, world"
    assert r.snapshot["stop_reason"] == "end_turn"
    print("ok,", len(r.events), "events")
