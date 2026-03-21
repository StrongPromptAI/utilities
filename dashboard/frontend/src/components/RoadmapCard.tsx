import { useState } from "react";
import { api } from "../api";
import type { RoadmapItem } from "../types";

interface Props {
  item: RoadmapItem;
  onUpdate: () => void;
}

const STATUS_ORDER: RoadmapItem["status"][] = ["planned", "building", "done"];

const CARD_BORDER: Record<RoadmapItem["status"], string> = {
  planned: "border-l-accent",
  building: "border-l-status-yellow",
  done: "border-l-status-green opacity-70",
};

const BADGE_CLASSES: Record<RoadmapItem["status"], string> = {
  planned: "bg-accent/15 text-accent",
  building: "bg-status-yellow/15 text-status-yellow",
  done: "bg-status-green/15 text-status-green",
};

export function RoadmapCard({ item, onUpdate }: Props) {
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(item.title);
  const [description, setDescription] = useState(item.description || "");
  const [round, setRound] = useState(item.round);
  const [status, setStatus] = useState(item.status);

  function cycleStatus() {
    const idx = STATUS_ORDER.indexOf(item.status);
    const next = STATUS_ORDER[(idx + 1) % STATUS_ORDER.length];
    api.updateRoadmapItem(item.id, { status: next }).then(onUpdate);
  }

  function save() {
    api
      .updateRoadmapItem(item.id, {
        title,
        description: description || undefined,
        round: round as RoadmapItem["round"],
        status,
      })
      .then(() => {
        setEditing(false);
        onUpdate();
      });
  }

  function remove() {
    if (!confirm("Delete this card?")) return;
    api.deleteRoadmapItem(item.id).then(onUpdate);
  }

  if (editing) {
    return (
      <div className="bg-surface-card border border-border border-l-[3px] border-l-status-purple rounded p-2.5 space-y-1.5">
        <input
          className="w-full bg-base text-body border border-border-input rounded px-2.5 py-1.5 text-sm"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Title"
        />
        <textarea
          className="w-full bg-base text-body border border-border-input rounded px-2.5 py-1.5 text-[13px] resize-y"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="Description (optional)"
          rows={2}
        />
        <div className="flex gap-1.5 items-center flex-wrap">
          <select
            value={round}
            onChange={(e) => setRound(Number(e.target.value) as RoadmapItem["round"])}
            className="bg-surface text-body border border-border-input rounded px-2 py-1 text-sm"
          >
            {[20, 40, 60, 80, 100].map((r) => (
              <option key={r} value={r}>{r}%</option>
            ))}
          </select>
          <select
            value={status}
            onChange={(e) => setStatus(e.target.value as RoadmapItem["status"])}
            className="bg-surface text-body border border-border-input rounded px-2 py-1 text-sm"
          >
            {STATUS_ORDER.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
          <button className="bg-btn text-white rounded px-3 py-1 text-sm hover:bg-btn-hover" onClick={save}>
            Save
          </button>
          <button className="bg-border text-muted rounded px-3 py-1 text-sm" onClick={() => setEditing(false)}>
            Cancel
          </button>
          <button className="bg-status-red-bg text-status-red rounded px-3 py-1 text-sm ml-auto" onClick={remove}>
            Delete
          </button>
        </div>
      </div>
    );
  }

  return (
    <div
      className={`bg-surface-card border border-border border-l-[3px] rounded p-2 cursor-grab hover:border-border-input transition-colors active:cursor-grabbing ${CARD_BORDER[item.status]}`}
      draggable
      onDragStart={(e) => {
        e.dataTransfer.setData("text/plain", String(item.id));
        e.dataTransfer.effectAllowed = "move";
      }}
      onClick={() => setEditing(true)}
    >
      <div className="flex items-start justify-between gap-1.5">
        <strong className="text-sm text-heading">{item.title}</strong>
        <button
          className={`text-[10px] px-1.5 py-0.5 rounded uppercase tracking-wide border-none cursor-pointer shrink-0 ${BADGE_CLASSES[item.status]}`}
          onClick={(e) => { e.stopPropagation(); cycleStatus(); }}
          title="Click to cycle status"
        >
          {item.status}
        </button>
      </div>
      {item.description && (
        <p className="text-xs text-muted mt-0.5 line-clamp-2">{item.description}</p>
      )}
    </div>
  );
}
