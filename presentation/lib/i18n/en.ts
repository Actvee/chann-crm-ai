import type { Dictionary } from "./th";

/**
 * English dictionary. Typed as Dictionary rather than inferred, so a missing
 * key is a compile error and an extra key is an excess-property error — both
 * directions of Spec 5.5's completeness check, enforced by tsc.
 */
export const en: Dictionary = {
  common: {
    save: "Save",
    cancel: "Cancel",
    confirm: "Confirm",
    delete: "Delete",
    edit: "Edit",
    close: "Close",
    loading: "Loading…",
    error: "Something went wrong",
    retry: "Try again",
    language: "Language",
  },
  liff: {
    starting: "Starting LIFF…",
    noCompany: "No company is linked to this account yet",
    multipleCompanies: "You belong to several companies — please choose one",
    notConfigured: "LIFF ID is not configured",
    initFailed: "LIFF initialisation failed",
    sdkLoadFailed: "Failed to load the LIFF SDK",
  },
  role: {
    title: "Role & Permission Management",
    permissionMatrix: "Permission Matrix",
    createCustomRole: "Create Custom Role",
    roleName: "Role name",
    permissionKeys: "Permission keys (comma separated)",
    createButton: "Create role",
    protectedOwner: "Owner role — protected",
  },
  licenseSetting: {
    title: "License Settings",
    settingKey: "Setting key",
    settingValue: "Value (JSON or text)",
    saveButton: "Save setting",
  },
  notification: {
    title: "Notifications",
    empty: "No notifications yet",
    markRead: "Mark as read",
    unreadBadge: "unread",
    loadFailed: "Could not load notifications",
  },
  customer: {
    title: "Customer",
    addNew: "Add New Customer",
  },
  deal: {
    title: "Deal",
    stage: {
      new: "New",
      proposed: "Proposed",
      won: "Won",
      lost: "Lost",
    },
  },
};
