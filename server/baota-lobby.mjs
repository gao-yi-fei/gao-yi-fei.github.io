import http from "node:http";
import { WebSocketServer } from "ws";

const PORT = Number(process.env.PORT || 8792);
const ROOM_TTL_MS = Number(process.env.ROOM_TTL_MS || 30000);
const HEARTBEAT_MS = Number(process.env.HEARTBEAT_MS || 10000);
const rooms = new Map();
const sockets = new Set();

function now() {
  return Date.now();
}

function publicRoom(room) {
  return {
    code: room.code,
    peerId: room.peerId,
    hostName: room.hostName,
    hostCharacter: room.hostCharacter,
    guestName: room.guestName || "",
    guestCharacter: room.guestCharacter || "",
    status: room.status,
    createdAt: room.createdAt,
    updatedAt: room.updatedAt,
  };
}

function listRooms() {
  const cutoff = now() - ROOM_TTL_MS;
  for (const [code, room] of rooms) {
    if (room.updatedAt < cutoff || room.status === "closed") rooms.delete(code);
  }
  return [...rooms.values()]
    .filter((room) => room.status !== "closed")
    .sort((a, b) => b.updatedAt - a.updatedAt)
    .map(publicRoom);
}

function send(ws, payload) {
  if (ws.readyState === ws.OPEN) ws.send(JSON.stringify(payload));
}

function broadcast(payload) {
  const text = JSON.stringify(payload);
  for (const ws of sockets) {
    if (ws.readyState === ws.OPEN) ws.send(text);
  }
}

function roomCode(value) {
  return String(value || "").trim().toUpperCase().replace(/[^A-Z0-9]/g, "").slice(0, 12);
}

function text(value, fallback = "") {
  return String(value || fallback).trim().replace(/\s+/g, " ").slice(0, 32);
}

function upsertRoom(message, owner) {
  const code = roomCode(message.code);
  if (!code) return null;
  const current = rooms.get(code) || {
    code,
    createdAt: now(),
    owner,
  };
  if (current.owner && current.owner !== owner) return current;
  current.owner = owner;
  current.peerId = text(message.peerId, `scpper-mc-${code.toLowerCase()}`);
  current.hostName = text(message.hostName, "房主");
  current.hostCharacter = text(message.hostCharacter, "未知角色");
  current.guestName = text(message.guestName, current.guestName || "");
  current.guestCharacter = text(message.guestCharacter, current.guestCharacter || "");
  current.status = ["waiting", "ready", "playing"].includes(message.status) ? message.status : "waiting";
  current.updatedAt = now();
  rooms.set(code, current);
  return current;
}

const server = http.createServer((req, res) => {
  if (req.url === "/health") {
    res.writeHead(200, { "content-type": "application/json; charset=utf-8", "access-control-allow-origin": "*" });
    res.end(JSON.stringify({ ok: true, rooms: listRooms().length }));
    return;
  }
  res.writeHead(404, { "content-type": "text/plain; charset=utf-8", "access-control-allow-origin": "*" });
  res.end("not found");
});

const wss = new WebSocketServer({ server });

wss.on("connection", (ws) => {
  sockets.add(ws);
  send(ws, { type: "rooms", rooms: listRooms(), now: now() });

  ws.on("message", (buffer) => {
    let message;
    try {
      message = JSON.parse(buffer.toString("utf8"));
    } catch {
      send(ws, { type: "error", message: "Bad JSON" });
      return;
    }

    if (message.type === "hello" || message.type === "list") {
      send(ws, { type: "rooms", rooms: listRooms(), now: now() });
      return;
    }

    if (message.type === "room") {
      const room = upsertRoom(message, ws);
      if (room) broadcast({ type: "rooms", rooms: listRooms(), now: now() });
      return;
    }

    if (message.type === "close") {
      const code = roomCode(message.code);
      const room = rooms.get(code);
      if (room && room.owner === ws) {
        room.status = "closed";
        room.updatedAt = now();
        rooms.delete(code);
        broadcast({ type: "rooms", rooms: listRooms(), now: now() });
      }
    }
  });

  ws.on("close", () => {
    sockets.delete(ws);
    let changed = false;
    for (const [code, room] of rooms) {
      if (room.owner === ws) {
        rooms.delete(code);
        changed = true;
      }
    }
    if (changed) broadcast({ type: "rooms", rooms: listRooms(), now: now() });
  });
});

setInterval(() => {
  broadcast({ type: "rooms", rooms: listRooms(), now: now() });
}, HEARTBEAT_MS).unref();

server.listen(PORT, () => {
  console.log(`SCPPER-MC Baota lobby listening on ${PORT}`);
});
