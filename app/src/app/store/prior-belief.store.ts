import { Injectable } from '@angular/core';
import { BehaviorSubject } from 'rxjs';
import { map } from 'rxjs/operators';
import { PriorBelief, PriorCondition } from '../models/prior-belief';

@Injectable({ providedIn: 'root' })
export class PriorBeliefStore {

  private priors$ = new BehaviorSubject<Record<string, PriorBelief>>({});

  all$ = this.priors$.asObservable();

  private key(datasetId: string, attribute: string, condition: PriorCondition): string {
    return `${datasetId}::${attribute}::${condition}`;
  }

  hasPriorsFor(datasetId: string): boolean {
    return Object.keys(this.priors$.value).some(k => k.startsWith(`${datasetId}::`));
  }

  hasPriorsFor$(datasetId: string) {
    return this.priors$.pipe(
      map(priors => Object.keys(priors).some(k => k.startsWith(`${datasetId}::`)))
    );
  }

  getBelief(datasetId: string, attribute: string, condition: PriorCondition): PriorBelief | undefined {
    return this.priors$.value[this.key(datasetId, attribute, condition)];
  }

  /** Returns true only when BOTH conditions have been elicited for the attribute. */
  hasBothBeliefs(datasetId: string, attribute: string): boolean {
    return !!this.getBelief(datasetId, attribute, 'diagnosed') &&
           !!this.getBelief(datasetId, attribute, 'not_diagnosed');
  }

  setBelief(belief: PriorBelief): void {
    const k = this.key(belief.datasetId, belief.attribute, belief.condition);
    const next = { ...this.priors$.value, [k]: belief };
    this.priors$.next(next);
  }

  removeBelief(datasetId: string, attribute: string, condition: PriorCondition): void {
    const next = { ...this.priors$.value };
    delete next[this.key(datasetId, attribute, condition)];
    this.priors$.next(next);
  }
}
