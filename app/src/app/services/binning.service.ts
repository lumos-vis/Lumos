
import { Injectable } from '@angular/core';
import { Bin } from '../models/prior-belief';

const BALL_COUNT = 30;

@Injectable({ providedIn: 'root' })
export class BinningService {

  readonly ballCount = BALL_COUNT;

  computeBins(values: number[]): Bin[] {
    const nums = values.map(Number);
    const rawMin = nums.reduce((a, b) => a < b ? a : b);
    const rawMax = nums.reduce((a, b) => a > b ? a : b);

    // One bin per integer value when range is small and all values are integers
    const allIntegers = nums.every(v => Number.isInteger(v));
    const range = rawMax - rawMin;
    if (allIntegers && range <= 10) {
      return Array.from({ length: range + 1 }, (_, i) => {
        const v = rawMin + i;
        return { lo: v, hi: v + 1, label: String(v) };
      });
    }

    const rawStep = range / 10;
    const step = this.niceStep(rawStep);
    const min = Math.floor(rawMin / step) * step;
    const max = Math.ceil(rawMax / step) * step;
    const count = Math.round((max - min) / step);

    return Array.from({ length: count }, (_, i) => {
      const lo = min + i * step;
      const hi = min + (i + 1) * step;
      return { lo, hi, label: `${this.fmt(lo, step)}–${this.fmt(hi, step)}` };
    });
  }

  emptyBallCounts(n = 10): number[] {
    return new Array(n).fill(0);
  }

  emptyCountsFor(n: number): number[] {
    return new Array(n).fill(0);
  }

  categoricalBins(categories: string[]): Bin[] {
    return categories.map((cat, i) => ({ lo: i, hi: i + 1, label: cat }));
  }

  private niceStep(rawStep: number): number {
    const magnitude = Math.pow(10, Math.floor(Math.log10(rawStep)));
    const normalized = rawStep / magnitude;
    let nice: number;
    if (normalized <= 1)      nice = 1;
    else if (normalized <= 2) nice = 2;
    else if (normalized <= 5) nice = 5;
    else                      nice = 10;
    return Math.max(nice * magnitude, 1);
  }

  private fmt(n: number, step: number): string {
    if (step >= 10)  return n.toFixed(0);
    if (step >= 1)   return n.toFixed(0);
    if (step >= 0.1) return n.toFixed(1);
    return n.toFixed(2);
  }
}
