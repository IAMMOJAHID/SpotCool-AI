import type { Zone } from './wire';

// Display names as in spotcool_contracts.zones.ZONES; a Record over the generated union, so a new
// zone in the contracts fails the build here.
const NAMES: Record<Zone, { name: string; role: string }> = {
  cor: { name: 'Core', role: 'Open-plan workspace' },
  nor: { name: 'North', role: 'Team area' },
  sou: { name: 'South', role: 'Meeting room' },
  eas: { name: 'East', role: 'Team area' },
  wes: { name: 'West', role: 'Reception and entrance' },
};

/** The five zones, in the contract's order. */
export const ZONES = Object.keys(NAMES) as Zone[];

export function isZone(value: string): value is Zone {
  return Object.hasOwn(NAMES, value);
}

export function zoneName(zone: Zone): string {
  return NAMES[zone].name;
}

export function zoneRole(zone: Zone): string {
  return NAMES[zone].role;
}
