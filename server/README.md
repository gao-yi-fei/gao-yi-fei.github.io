# 暴塔大厅后端

这是 `game.html` 使用的可选 WebSocket 房间大厅后端，只维护房间列表和心跳，不参与战斗结算。

## 本地运行

```bash
npm install
npm start
```

默认监听 `8792`：

```text
ws://127.0.0.1:8792
```

## 前端配置

线上如果部署了自己的后端，把 `assets/baota-config.js` 改成：

```js
window.BAOTA_LOBBY_URL = "wss://你的大厅后端";
```

没有配置时，前端会退回公共 MQTT WebSocket 大厅；手动房间码仍然可用。
