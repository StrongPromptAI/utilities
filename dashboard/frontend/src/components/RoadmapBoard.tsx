import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { RoadmapItem } from "../types";
import { RoadmapCard } from "./RoadmapCard";

interface Props {
  projectId: number | null;
}

const SPOKES = ["doctor", "patient", "dme", "pt"] as const;
const ROUNDS = [20, 40, 60, 80, 100] as const;
const STATUSES: RoadmapItem["status"][] = ["planned", "building", "done"];

const LANE_HEADER_CLASSES: Record<RoadmapItem["status"], string> = {
  planned: "text-accent bg-accent/10",
  building: "text-status-yellow bg-status-yellow/10",
  done: "text-status-green bg-status-green/10",
};

export function RoadmapBoard({ projectId }: Props) {
  const [spoke, setSpoke] = useState<(typeof SPOKES)[number]>("doctor");
  const [items, setItems] = useState<RoadmapItem[]>([]);
  const [adding, setAdding] = useState<number | null>(null);
  const [newTitle, setNewTitle] = useState("");
  const [dropTarget, setDropTarget] = useState<string | null>(null);

  const reload = useCallback(() => {
    if (!projectId) return;
    api.roadmapItems(projectId, spoke).then(setItems);
  }, [projectId, spoke]);

  useEffect(() => {
    if (!projectId) {
      setItems([]);
      return;
    }
    reload();
  }, [projectId, spoke, reload]);

  function addCard(round: number) {
    if (!projectId || !newTitle.trim()) return;
    api
      .createRoadmapItem({
        project_id: projectId,
        title: newTitle.trim(),
        spoke,
        round,
      })
      .then(() => {
        setNewTitle("");
        setAdding(null);
        reload();
      });
  }

  if (!projectId) {
    return <p className="text-muted text-sm">Select a project to view roadmap</p>;
  }

  return (
    <div className="flex flex-col gap-6">
      {/* Spoke tabs */}
      <div className="flex gap-2">
        {SPOKES.map((s) => (
          <button
            key={s}
            className={`px-4 py-1.5 rounded-2xl text-sm font-medium transition-colors ${
              spoke === s
                ? "bg-btn text-white"
                : "bg-border text-muted hover:text-body"
            }`}
            onClick={() => setSpoke(s)}
          >
            {s.toUpperCase()}
          </button>
        ))}
      </div>

      {/* Rounds */}
      {ROUNDS.map((round) => {
        const roundItems = items.filter((i) => i.round === round);

        return (
          <div key={round} className="bg-surface border border-border rounded-md p-4">
            {/* Round header */}
            <div className="flex items-center gap-2 font-semibold text-heading mb-3 pb-2 border-b border-border">
              <span>{round}% Round</span>
              <span className="bg-border/50 px-2 py-0.5 rounded-lg text-xs text-muted">
                {roundItems.length}
              </span>
            </div>

            {/* Lanes: planned | building | done */}
            <div className="grid grid-cols-3 gap-3 min-h-[40px]">
              {STATUSES.map((status) => {
                const lane = roundItems.filter((i) => i.status === status);
                const laneKey = `${round}-${status}`;
                const isOver = dropTarget === laneKey;
                return (
                  <div
                    key={status}
                    className={`flex flex-col gap-1.5 rounded-md p-1 -m-1 transition-colors ${isOver ? "bg-accent/10 ring-1 ring-accent/30" : ""}`}
                    onDragOver={(e) => {
                      e.preventDefault();
                      e.dataTransfer.dropEffect = "move";
                      setDropTarget(laneKey);
                    }}
                    onDragLeave={() => setDropTarget(null)}
                    onDrop={(e) => {
                      e.preventDefault();
                      setDropTarget(null);
                      const itemId = Number(e.dataTransfer.getData("text/plain"));
                      if (!itemId) return;
                      const item = items.find((i) => i.id === itemId);
                      if (!item || (item.status === status && item.round === round)) return;
                      api.updateRoadmapItem(itemId, { status, round: round as RoadmapItem["round"] }).then(reload);
                    }}
                  >
                    <div
                      className={`text-xs font-semibold uppercase tracking-wide px-2 py-1 rounded ${LANE_HEADER_CLASSES[status]}`}
                    >
                      {status}
                    </div>
                    {lane.map((item) => (
                      <RoadmapCard key={item.id} item={item} onUpdate={reload} />
                    ))}
                  </div>
                );
              })}
            </div>

            {/* Add card */}
            {adding === round ? (
              <div className="flex gap-1.5 items-center mt-2">
                <input
                  className="flex-1 bg-base text-body border border-border-input rounded px-2.5 py-1.5 text-sm"
                  value={newTitle}
                  onChange={(e) => setNewTitle(e.target.value)}
                  placeholder="Card title..."
                  autoFocus
                  onKeyDown={(e) => {
                    if (e.key === "Enter") addCard(round);
                    if (e.key === "Escape") setAdding(null);
                  }}
                />
                <button
                  className="bg-btn text-white rounded px-3 py-1.5 text-sm hover:bg-btn-hover"
                  onClick={() => addCard(round)}
                >
                  Add
                </button>
                <button
                  className="bg-border text-muted rounded px-3 py-1.5 text-sm"
                  onClick={() => setAdding(null)}
                >
                  Cancel
                </button>
              </div>
            ) : (
              <button
                className="w-full mt-2 border border-dashed border-border-input rounded text-faint text-sm py-1.5 hover:border-accent hover:text-accent transition-colors"
                onClick={() => { setAdding(round); setNewTitle(""); }}
              >
                + Add card
              </button>
            )}
          </div>
        );
      })}
    </div>
  );
}
