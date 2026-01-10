const adjectives = [
  'swift', 'bright', 'calm', 'bold', 'wise', 'keen', 'pure', 'wild', 'cool', 'warm',
  'clear', 'deep', 'fair', 'fine', 'free', 'glad', 'good', 'kind', 'neat', 'nice',
  'quick', 'rare', 'real', 'safe', 'sure', 'true', 'vast', 'vivid', 'young', 'zesty',
  'amber', 'azure', 'coral', 'crimson', 'golden', 'jade', 'lunar', 'misty', 'noble', 'royal',
  'silver', 'stellar', 'cosmic', 'divine', 'epic', 'mystic', 'primal', 'quantum', 'radiant', 'serene'
];

const nouns = [
  'theorem', 'proof', 'lemma', 'axiom', 'logic', 'truth', 'reason', 'notion', 'concept', 'idea',
  'thought', 'theory', 'method', 'system', 'model', 'pattern', 'structure', 'formula', 'function', 'relation',
  'element', 'factor', 'vector', 'matrix', 'tensor', 'field', 'space', 'domain', 'range', 'limit',
  'summit', 'peak', 'zenith', 'apex', 'vertex', 'nexus', 'core', 'essence', 'spark', 'flame',
  'wave', 'pulse', 'flow', 'stream', 'river', 'ocean', 'sky', 'star', 'moon', 'sun'
];

export function generateRandomName(): string {
  const adjective = adjectives[Math.floor(Math.random() * adjectives.length)];
  const noun = nouns[Math.floor(Math.random() * nouns.length)];
  return `${adjective}-${noun}`;
}
