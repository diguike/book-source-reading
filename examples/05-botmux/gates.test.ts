// 验证两道闸，重点是 PR #46 那个洞：只配了对话授权时，不能退化成全开放。
import { canOperate, canTalk, type BotPerm } from './gates.ts';

const base: BotPerm = { allowedUsers: [], allowedChatGroups: [], talkGrants: [], peerBots: new Set(['bot_peer']) };
const cases: [string, BotPerm, string, string | undefined][] = [
  ['open 模式', base, 'stranger', undefined],
  ['只配 talkGrants', { ...base, talkGrants: ['guest'] }, 'guest', undefined],
  ['只配 talkGrants 的陌生人', { ...base, talkGrants: ['guest'] }, 'stranger', undefined],
  ['oncall 群成员', { ...base, allowedUsers: ['owner'], allowedChatGroups: ['oc_1'] }, 'member', 'oc_1'],
  ['owner', { ...base, allowedUsers: ['owner'] }, 'owner', undefined],
  ['兄弟 bot', { ...base, allowedUsers: ['owner'] }, 'bot_peer', undefined],
];
for (const [name, p, who, chat] of cases) {
  console.log(name.padEnd(14), 'talk', canTalk(p, who, chat), 'operate', canOperate(p, who));
}
