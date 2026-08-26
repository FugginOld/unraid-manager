# Unraid Multi-NAS Fleet & Transfer Manager: Functional & Technical Requirements Specification

**Document Version:** 1.0.0  
**Target Platform:** Unraid OS 7.0+ (Dynamix WebGUI Architecture)  
**Purpose:** Specification requirements for automated LLM/code generators to synthesize backend controllers, GraphQL integration drivers, and WebGUI plugin templates.

---

## 1. System Overview & Architecture

The **Unraid Multi-NAS Manager** is a centralized control plane designed as an Unraid WebGUI plugin (`/usr/local/emhttp/plugins/multi-nas-manager/`). It unifies multi-node telemetry, node power/terminal operations, and granular server-to-server data orchestration across heterogeneous or homogeneous Unraid storage arrays.

```
+-----------------------------------------------------------------------------------+
|                            Unraid WebGUI Plugin UI                                |
|  +-------------------------------------+  +------------------------------------+  |
|  |     Tab 1: Fleet Manager            |  |     Tab 2: Transfer Manager        |  |
|  +-------------------------------------+  +------------------------------------+  |
+-----------------------------------------------------------------------------------+
                                         |
                                         v
+-----------------------------------------------------------------------------------+
|                        PHP / Node Middleware Controller                           |
|  - AES-256 Vault (API Keys)   - GraphQL / REST Driver   - Rsync Engine Wrapper    |
+-----------------------------------------------------------------------------------+
       |                                   |                                  |
       v                                   v                                  v
+---------------+                   +---------------+                  +---------------+
| Remote NAS 1  |                   | Remote NAS 2  |                  | Remote NAS 3  |
| (Unraid 7+)   |                   | (Unraid 7+)   |                  | (Unraid 7+)   |
+---------------+                   +---------------+                  +---------------+
```

### 1.1 Tech Stack Standard
* **Frontend:** Unraid Dynamix WebGUI design system (HTML5, Native Unraid CSS Variables, Vanilla JS ES6+ / Vue.js 3 light runtime).
* **Plugin Architecture:** Native Unraid `.page` configuration schema (`/usr/local/emhttp/plugins/multi-nas-manager/multi-nas-manager.page`).
* **Backend Runtime:** PHP 8.2+ (Unraid native web server integration) and Python 3.11+ / Bash execution daemons.
* **API Telemetry Layer:** Unraid 7 GraphQL API (`/graphql`) with REST/JSON-RPC fallback (`/api/v1`).
* **Transfer Execution Engine:** Server-to-server SSH key-authenticated `rsync` (v3.2+) and `rclone` binary wrappers executing inside isolated background `screen` or `systemd-run` sessions.

---

## 2. Unraid Storage Layer & Mount Mapping

The system must directly interface with Unraid's distinct physical and virtual storage mount points. All file browsers and transfer engines must respect and explicitly expose these path mappings:

| Storage Layer | Path Prefix | Access Mechanism | Operational Characteristics |
|---|---|---|---|
| **User Shares (Pooled)** | `/mnt/user/<share>` | Fuse-over-shfs | Abstracted virtual pool combining Array Disks + Cache Pools using share allocation rules (High-Water, Fill-Up, Most-Free). |
| **Bypass Cache Array** | `/mnt/user0/<share>` | Fuse-over-shfs | Direct array access; forces writes directly to parity-protected array, completely bypassing primary/secondary cache pools. |
| **Physical Array Disks** | `/mnt/disk<N>/<share>` | Direct POSIX | Pinpoint access to specific physical drives (e.g., `/mnt/disk1`, `/mnt/disk2`). Essential for disk balancing and array maintenance. |
| **Cache Pools** | `/mnt/<pool_name>/<share>` | Direct ZFS / Btrfs | High-speed direct access to dedicated NVMe or SATA SSD pools (e.g., `/mnt/cache`, `/mnt/fast_nvme`, `/mnt/download_pool`). |

---

## 3. Data Models & API Schemas

### 3.1 Node Configuration Model (`NodeConfig`)
```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "properties": {
    "node_id": { "type": "string", "format": "uuid" },
    "name": { "type": "string" },
    "hostname": { "type": "string" },
    "port": { "type": "integer", "default": 443 },
    "use_ssl": { "type": "boolean", "default": true },
    "api_token": { "type": "string", "description": "AES-256 Encrypted Token" },
    "ssh_port": { "type": "integer", "default": 22 },
    "ssh_key_path": { "type": "string" },
    "status": { "type": "string", "enum": ["ONLINE", "OFFLINE", "DEGRADED", "SYNCING"] }
  },
  "required": ["node_id", "name", "hostname", "api_token"]
}
```

### 3.2 Telemetry Snapshot Schema (`NodeTelemetry`)
```json
{
  "node_id": "uuid-string",
  "timestamp": "2026-08-26T13:19:44Z",
  "array_status": "STARTED", // "STARTED", "STOPPED", "PARITY_CHECK", "REBUILDING"
  "cpu_utilization_pct": 14.2,
  "ram_utilization_pct": 48.6,
  "cpu_temp_celsius": 42.0,
  "array_capacity": {
    "total_bytes": 140737488355328,
    "used_bytes": 85759160320000,
    "free_bytes": 54978328035328
  },
  "disks": [
    {
      "identifier": "disk1",
      "device": "sdb",
      "temp_celsius": 32,
      "smart_status": "PASSED",
      "size_bytes": 16000000000000,
      "used_bytes": 12000000000000
    }
  ]
}
```

### 3.3 Rule Engine Task Schema (`TransferJob`)
```json
{
  "job_id": "job-uuid-12345",
  "source_node_id": "node-uuid-alpha",
  "source_scope": "ARRAY_DISK", // "USER_SHARE", "ARRAY_DISK", "CACHE_POOL"
  "source_path": "/mnt/disk2/media/movies",
  "target_node_id": "node-uuid-beta",
  "target_scope": "CACHE_POOL",
  "target_path": "/mnt/fast_cache/ingest",
  "execution_mode": "MOVE", // "COPY", "MOVE", "DRY_RUN"
  "rules": {
    "file_age": {
      "operator": "GREATER_THAN", // "GREATER_THAN", "LESS_THAN"
      "value": 30,
      "unit": "DAYS" // "HOURS", "DAYS", "MONTHS"
    },
    "extensions": [".mkv", ".iso", ".tar.gz"],
    "pattern_match": {
      "regex": ".*2025.*",
      "case_sensitive": false
    },
    "min_size_mb": 500,
    "max_size_mb": null
  },
  "options": {
    "preserve_permissions": true,
    "verify_checksum": true,
    "bandwidth_limit_kbps": 0,
    "delete_empty_src_dirs": true
  }
}
```

---

## 4. Tab 1 Requirements: Unraid Fleet Manager

### 4.1 UI Component Architecture
1. **Header Toolbar:**
   * Global Fleet Summary Bar (Total Storage Across All Nodes, Active Transfer Count, Global Alert Count).
   * `+ Add New Unraid Node` Button (triggers modal for Hostname, API Key, SSH credentials).
   * Global Refresh Rate Selector (`5s`, `10s`, `30s`, `Manual`).

2. **Server Instance Cards Grid:**
   * Responsive layout auto-fitting cards for each registered Unraid instance.
   * **Card Header:** Node Name, Hostname/IP Badge, Connection Status Badge (Green dot: Connected, Red: Offline, Amber: Syncing/Parity Check).
   * **Hardware Telemetry Gauges:**
     * CPU Load (% meter + Sparkline history).
     * RAM Usage (% meter + MB used/free).
     * CPU Temperature (°C indicator with warning threshold > 75°C).
   * **Storage Array Telemetry:**
     * Array Status Banner (`Array Started`, `Array Stopped`, `Parity Sync - 42%`).
     * Visual Progress Bar: Total Array Capacity vs. Used Capacity (TB and %).
     * Parity & Disk Health Badges (e.g., `12/12 Disks Passed SMART`, `0 Sector Errors`).
   * **Action Button Toolbar (Footer of each card):**
     * **`Start / Stop Array` Toggle:** Primary button. Requires a two-step confirmation popover (`Are you sure you want to stop the array on unraid-main?`). Disabled if transfers are active.
     * **`Reboot` Button:** Secondary danger button. Prompts safety check (verifies array unmount state).
     * **`Web Terminal` Button:** Opens a modal window initializing a secured SSH/ttyd terminal session directly connected to that node's shell.

### 4.2 Functional Requirements & API Integration

#### Requirement 1.1: Multi-Node Telemetry Polling
* **Behavior:** The plugin controller must issue asynchronous parallel requests to all registered nodes using their stored API keys.
* **Unraid 7 GraphQL Telemetry Query:**
  ```graphql
  query GetNodeTelemetry {
    system {
      cpu { usage, temperature }
      memory { total, used, free }
      array {
        state
        capacity { total, used, free }
        disks { id, name, temp, smartStatus }
      }
    }
  }
  ```
* **Fallback:** If GraphQL is unavailable, issue HTTP GET to native JSON endpoints (`/api/v1/system/status`).

#### Requirement 1.2: Node Array Control Operations
* **Start Array API Command:**  
  `POST /api/v1/array/start` or GraphQL Mutation `mutation { arrayStart { success message } }`.
* **Stop Array API Command:**  
  `POST /api/v1/array/stop` or GraphQL Mutation `mutation { arrayStop { success message } }`.
* **Safety Lock:** Disable "Stop Array" if an active `TransferJob` is reading/writing to target node storage.

#### Requirement 1.3: Web Terminal Integration
* **Implementation:** Embed an `iframe` pointing to `https://<NODE_IP>:<PORT>/ttyd/` or proxy web sockets via the local plugin controller (`ws://localhost/api/v1/terminal/proxy?node_id=<UUID>`).

---

## 5. Tab 2 Requirements: NAS Transfer Manager

### 5.1 UI Component Architecture

1. **Top Scope & Navigation Selector Panel:**
   * **Source Node Column (Left):**
     * Node Dropdown Selector (`unraid-main`, `unraid-backup`, etc.).
     * Storage Layer Segmented Selector: `[ User Shares (/mnt/user) | Array Disks (/mnt/diskN) | Cache Pools (/mnt/pool) ]`.
     * Directory Tree Explorer dropdown / breadcrumb path bar.
   * **Destination Node Column (Right):**
     * Node Dropdown Selector (`unraid-main`, `unraid-backup`, etc.).
     * Storage Layer Segmented Selector: `[ User Shares (/mnt/user) | Array Disks (/mnt/diskN) | Cache Pools (/mnt/pool) ]`.
     * Directory Tree Explorer dropdown / breadcrumb path bar.

2. **Advanced Rule & Filter Engine Panel (Collapsible Box):**
   * **Execution Mode Switch:** `[ Copy (Keep Source) | Move (Delete Source) | Dry Run (Simulate) ]`.
   * **Filter Option 1: File Age Filter**
     * Toggle Switch: Enable/Disable File Age.
     * Mode: `Older Than` vs. `Newer Than`.
     * Numeric Input + Unit Dropdown (`Days`, `Hours`, `Months`). Maps to POSIX `mtime` / `ctime`.
   * **Filter Option 2: File Extensions & Types**
     * Category Quick-Select Checkboxes: `[ Videos (.mkv, .mp4) | Disk Images (.iso, .img) | Archives (.zip, .tar, .gz) | Backups (.bak, .sql) ]`.
     * Custom Extension Tag Input (comma-separated string e.g. `.log, .tmp, .nfo`).
   * **Filter Option 3: String & Pattern Search Filter**
     * Name Match String input field (e.g. `season_01`, `backup_2026`).
     * RegEx Switch (Toggle between Substring search and full Regex evaluation).
   * **Filter Option 4: File Size Thresholds**
     * Min Size (MB/GB) and Max Size (MB/GB) numeric bounds.

3. **File Match Preview & Staging Area:**
   * **`Preview Filtered Files` Button:** Queries the source node and renders a table of matched files before transfer.
   * **Table Columns:** `[ Checkbox | File Name | Source Path | Size | Last Modified | Target Path Preview ]`.
   * **Summary Bar:** `Total Files Matched: 42 | Total Data Size: 128.4 GB`.

4. **Transfer Execution & Live Monitor Panel:**
   * **`Execute Transfer` Button:** Triggers backend process generation.
   * **Live Job Monitor Drawer / Progress Modal:**
     * Overall Job Progress Bar (%).
     * Current File Transfer Progress.
     * Live Throughput Speed Indicator (e.g. `340.5 MB/s` over 10GbE).
     * Estimated Time Remaining (ETA).
     * Collapsible Real-Time Log Console (Streaming stdout/stderr from backend `rsync`).

---

## 6. Transfer Engine Specifications & Commands

### 6.1 Backend Process Execution Strategy
To handle multi-gigabyte/terabyte transfers without web server timeout or browser tab dependency, all operations MUST execute as detached background daemons on the host Unraid server.

1. **SSH Key Pair Exchange:** The master Unraid server hosting the plugin generates an SSH keypair (`/root/.ssh/id_rsa_multinas`) and distributes the public key to all target Unraid instances during node onboarding.
2. **Command Assembly Algorithm:**

#### Scenario A: Move files older than 30 days matching `.mkv` from Array Disk 2 to Remote NVMe Pool
```bash
# Generated Execution Command
rsync -avPR --remove-source-files --checksum   --files-from=<(ssh -p 22 root@192.168.1.100 "find /mnt/disk2/media/movies -type f -name '*.mkv' -mtime +30")   root@192.168.1.100:/   root@192.168.1.105:/mnt/fast_cache/ingest/   --progress --log-file=/var/log/multi-nas-manager/job-12345.log
```

#### Scenario B: Sync User Share to Remote User Share with Bandwidth Limit
```bash
# Generated Execution Command
rsync -avzP --bwlimit=50000 --checksum   --include="*/" --include="*.iso" --exclude="*"   root@192.168.1.100:/mnt/user/isos/   root@192.168.1.105:/mnt/user/backups/isos/   --log-file=/var/log/multi-nas-manager/job-67890.log
```

### 6.2 Progress Parsing Regex
The backend controller must monitor `/var/log/multi-nas-manager/job-<ID>.log` and parse lines with the following expression:
```regex
(?P<bytes_transferred>\d+)\s+(?P<percentage>\d+)%\s+(?P<speed>[\d\.]+[kB/s|MB/s|GB/s]+)\s+(?P<eta>[\d:]+)
```

---

## 7. Non-Functional & Security Requirements

1. **Credential Storage Safety:**
   * All API keys and SSH private keys stored in `/boot/config/plugins/multi-nas-manager/credentials.json` MUST be encrypted using AES-256-GCM. The decryption key must be derived from Unraid's unique hardware machine ID (`/etc/machine-id`).

2. **Error Recovery & Resiliency:**
   * **Network Drop:** `rsync` jobs must utilize `--partial` and `--append-verify` flags so interrupted transfers resume automatically without re-downloading existing chunks.
   * **Array Stop Protection:** The plugin daemon must listen for Unraid array shutdown signals (`/usr/local/emhttp/plugins/dynamix/scripts/stop_array`) and gracefully send `SIGTERM` to active transfer jobs before unmounting disks.

3. **Logging & Auditing:**
   * Comprehensive audit trail saved to `/var/log/multi-nas-manager/audit.log`.
   * Log entries must record: `Timestamp`, `User`, `Action` (e.g. Array Stop, File Transfer), `Source Node`, `Destination Node`, `Files Affected`, and `Execution Status`.

---

## 8. Implementation Checklist for Code Generators

When using this requirements document to prompt automated LLM code generators, generate files in the following sequence:

1. `multi-nas-manager.page` - Dynamix WebGUI menu integration file.
2. `api/config.php` - Secure credential storage and node management handler.
3. `api/telemetry.php` - Asynchronous GraphQL polling controller for Tab 1.
4. `api/transfer.php` - Rules parser, file scanner (`find` wrapper), and `rsync` builder for Tab 2.
5. `assets/js/fleet-manager.js` - Tab 1 Vue/Vanilla JS frontend logic.
6. `assets/js/transfer-manager.js` - Tab 2 dual-pane UI and real-time streaming progress reader.
