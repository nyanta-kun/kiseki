"use client";

import { useState } from "react";
import { UsersTab } from "./UsersTab";
import { CodesTab } from "./CodesTab";
import { DataTab } from "./DataTab";
import { SettingsTab } from "./SettingsTab";

export type User = {
  id: number;
  email: string;
  name: string | null;
  image_url: string | null;
  role: string;
  is_active: boolean;
  is_premium: boolean;
  access_expires_at: string | null;
  created_at: string;
  last_login_at: string | null;
  yoso_name: string | null;
  is_yoso_public: boolean;
};

export type InvitationCode = {
  id: number;
  code: string;
  grant_type: string;
  weeks_count: number | null;
  target_date: string | null;
  max_uses: number;
  use_count: number;
  is_active: boolean;
  note: string | null;
  created_at: string;
};

type Tab = "users" | "data" | "settings";

const TABS: { id: Tab; label: string }[] = [
  { id: "users", label: "ユーザー" },
  { id: "data", label: "データ" },
  { id: "settings", label: "設定" },
];

interface AdminTabsProps {
  users: User[];
  codes: InvitationCode[];
}

export function AdminTabs({ users, codes }: AdminTabsProps) {
  const [activeTab, setActiveTab] = useState<Tab>("users");

  return (
    <div>
      {/* タブナビ */}
      <div className="border-b border-gray-200 mb-6">
        <nav className="-mb-px flex space-x-6">
          {TABS.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`py-3 px-1 border-b-2 text-sm font-medium transition-colors ${
                activeTab === tab.id
                  ? "border-[#0d1f35] text-[#0d1f35]"
                  : "border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300"
              }`}
            >
              {tab.label}
            </button>
          ))}
        </nav>
      </div>

      {/* タブコンテンツ */}
      {activeTab === "users" && <UsersTab users={users} codes={codes} />}
      {activeTab === "data" && <DataTab />}
      {activeTab === "settings" && <SettingsTab />}
    </div>
  );
}
