// Every tunable of the relay: lifetimes in seconds, body caps in bytes, rate limits per token or hashed IP per hour.

export const HOUR = 3600;
export const DAY = 86400;
export const TTL = { code: 15 * 60, invite: 72 * HOUR, inviteKept: 90 * DAY, park: DAY, ack: 7 * DAY };
export const MAX = { form: 4 * 1024, relay: 256 * 1024, intake: 8 * 1024, ack: 4 * 1024, publish: 5 * 1024 * 1024,
  inviteIds: 100, tokenDays: 366 };
export const LIMITS = { install: 10, professional: 30, forward: 200, ack: 60, join: 20, pro: 20, enroll: 40 };
