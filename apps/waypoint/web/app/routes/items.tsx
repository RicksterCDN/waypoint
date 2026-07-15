/**
 * Items page - demonstrates API integration with the FastAPI backend.
 *
 * Features:
 * - Fetches items from /api/items
 * - Creates new items via form submission
 * - Deletes items
 * - All API calls are automatically traced via OpenTelemetry
 */

import type { MetaFunction } from "react-router";
import { Link } from "react-router";
import { useState, useEffect, useCallback } from "react";
import {
  HiOutlineTrash,
  HiOutlinePlus,
  HiOutlineRefresh,
} from "react-icons/hi";
import { RequireAuth } from "../components/RequireAuth";

export const meta: MetaFunction = () => {
  return [
    { title: "Items - FastAPI React Aspire Starter" },
    { name: "description", content: "Demo page showing API integration" },
  ];
};

interface Item {
  id: string;
  name: string;
  description: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

async function tracedFetch(
  name: string,
  input: RequestInfo | URL,
  init?: RequestInit,
): Promise<Response> {
  const { traced } = await import("../../lib/telemetry");
  return traced(name, () => fetch(input, init));
}

export default function Items() {
  const [items, setItems] = useState<Item[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [newItemName, setNewItemName] = useState("");
  const [newItemDescription, setNewItemDescription] = useState("");

  // Fetch items from API
  const fetchItems = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await tracedFetch("fetchItems", "/api/items/");
      if (!response.ok) {
        throw new Error(`Failed to fetch items: ${response.statusText}`);
      }
      const data = await response.json();
      setItems(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch items");
    } finally {
      setLoading(false);
    }
  }, []);

  // Load items on mount
  useEffect(() => {
    fetchItems();
  }, [fetchItems]);

  // Create a new item
  const handleCreateItem = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newItemName.trim()) return;

    try {
      const response = await tracedFetch("createItem", "/api/items/", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: newItemName,
          description: newItemDescription,
        }),
      });

      if (!response.ok) {
        throw new Error(`Failed to create item: ${response.statusText}`);
      }

      // Clear form and refresh list
      setNewItemName("");
      setNewItemDescription("");
      fetchItems();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create item");
    }
  };

  // Delete an item
  const handleDeleteItem = async (itemId: string) => {
    try {
      const response = await tracedFetch("deleteItem", `/api/items/${itemId}`, {
        method: "DELETE",
      });

      if (!response.ok && response.status !== 204) {
        throw new Error(`Failed to delete item: ${response.statusText}`);
      }

      fetchItems();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete item");
    }
  };

  return (
    <RequireAuth>
      <div className="min-h-screen flex flex-col">
      {/* Header */}
      <header className="border-b border-zinc-200 dark:border-zinc-800">
        <div className="max-w-3xl mx-auto px-4 py-4 flex items-center justify-between">
          <div className="flex items-center gap-4">
            <Link
              to="/"
              className="text-blue-600 dark:text-blue-400 hover:underline"
            >
              ← Home
            </Link>
            <h1 className="text-xl font-bold">Items</h1>
          </div>
          <button
            onClick={fetchItems}
            className="p-2 text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300"
            title="Refresh"
          >
            <HiOutlineRefresh
              className={`w-5 h-5 ${loading ? "animate-spin" : ""}`}
            />
          </button>
        </div>
      </header>

      {/* Main content */}
      <main className="flex-1 max-w-3xl mx-auto px-4 py-8 w-full">
        {/* Create form */}
        <form onSubmit={handleCreateItem} className="mb-8">
          <div className="flex gap-4">
            <div className="flex-1">
              <input
                type="text"
                value={newItemName}
                onChange={(e) => setNewItemName(e.target.value)}
                placeholder="Item name"
                className="w-full px-4 py-2 border border-zinc-300 dark:border-zinc-600 rounded-lg bg-white dark:bg-zinc-800 focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                required
              />
            </div>
            <div className="flex-1">
              <input
                type="text"
                value={newItemDescription}
                onChange={(e) => setNewItemDescription(e.target.value)}
                placeholder="Description (optional)"
                className="w-full px-4 py-2 border border-zinc-300 dark:border-zinc-600 rounded-lg bg-white dark:bg-zinc-800 focus:ring-2 focus:ring-blue-500 focus:border-transparent"
              />
            </div>
            <button
              type="submit"
              className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors flex items-center gap-2"
            >
              <HiOutlinePlus className="w-5 h-5" />
              Add
            </button>
          </div>
        </form>

        {/* Error message */}
        {error && (
          <div className="mb-4 p-4 bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-300 rounded-lg">
            {error}
          </div>
        )}

        {/* Items list */}
        {loading && items.length === 0 ? (
          <div className="text-center py-12 text-zinc-500">Loading...</div>
        ) : items.length === 0 ? (
          <div className="text-center py-12 text-zinc-500">
            No items yet. Create one above!
          </div>
        ) : (
          <div className="space-y-4">
            {items.map((item) => (
              <div
                key={item.id}
                className="border border-zinc-200 dark:border-zinc-700 rounded-lg p-4 flex items-start justify-between"
              >
                <div>
                  <h3 className="font-semibold">{item.name}</h3>
                  {item.description && (
                    <p className="text-sm text-zinc-600 dark:text-zinc-400 mt-1">
                      {item.description}
                    </p>
                  )}
                  <p className="text-xs text-zinc-400 dark:text-zinc-500 mt-2">
                    ID: {item.id} • Created:{" "}
                    {new Date(item.created_at).toLocaleString()}
                  </p>
                </div>
                <button
                  onClick={() => handleDeleteItem(item.id)}
                  className="p-2 text-red-500 hover:text-red-700 hover:bg-red-100 dark:hover:bg-red-900/30 rounded-lg transition-colors"
                  title="Delete item"
                >
                  <HiOutlineTrash className="w-5 h-5" />
                </button>
              </div>
            ))}
          </div>
        )}

        {/* Info box */}
        <div className="mt-8 p-4 bg-blue-50 dark:bg-blue-900/20 rounded-lg text-sm">
          <p className="text-blue-800 dark:text-blue-200">
            <strong>💡 Tip:</strong> Open the Aspire dashboard to see traces for
            these API calls. Each fetch, create, and delete operation creates
            spans that are visible in the distributed tracing view.
          </p>
        </div>
      </main>
      </div>
    </RequireAuth>
  );
}
