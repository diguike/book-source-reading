// 对话闸和操作闸的最小实现。对应第 5 章 4.4 节。
export interface BotPerm {
  allowedUsers: string[];      // owner：能对话，也能操作
  allowedChatGroups: string[]; // 这些群里的人：只能对话
  talkGrants: string[];        // 被 grant 的人或 bot：只能对话
  peerBots: Set<string>;       // 同部署的兄弟 bot：两样都能
}

// 配了任何名单就算限制态。talk 名单只参与这一步，用来判断是不是 open 模式
const restricted = (p: BotPerm) =>
  p.allowedUsers.length + p.allowedChatGroups.length + p.talkGrants.length > 0;

export function canTalk(p: BotPerm, who: string, chatId?: string): boolean {
  if (p.allowedUsers.includes(who) || p.peerBots.has(who)) return true;
  if (chatId && p.allowedChatGroups.includes(chatId)) return true;
  if (!restricted(p)) return true;                       // open 模式
  return p.talkGrants.includes(who);
}

export function canOperate(p: BotPerm, who: string): boolean {
  if (p.peerBots.has(who)) return true;
  if (!restricted(p)) return true;                       // open 模式
  return p.allowedUsers.includes(who);                   // 放行只认 owner
}

const OPERATE_CMDS = new Set(['/repo', '/cd', '/relay', '/config']);
export const gateFor = (cmd: string) => (OPERATE_CMDS.has(cmd) ? canOperate : canTalk);
