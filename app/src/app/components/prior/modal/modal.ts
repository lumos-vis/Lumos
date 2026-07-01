import { Component, Input, Output, EventEmitter } from '@angular/core';
import { Bin, PriorBelief, PriorCondition } from '../../../models/prior-belief';
import { PriorBeliefStore } from '../../../store/prior-belief.store';
import { BinningService } from '../../../services/binning.service';
import { ChatService } from '../../../services/socket.service';
import { SessionPage } from '../../../models/config';

const ATTR_LABELS: Record<string, string> = {
  child_age_years:               'Age (years)',
  child_sex:                     'Sex',
  screen_time_weekday:           'Daily Screen Time (hours)',
  hours_sleep_weeknight:         'Sleep Hours (weeknight)',
  days_physical_activity_week:   'Physical Activity (days/week)',
  difficulty_making_friends:     'Difficulty Making Friends',
  ever_diagnosed_depression:     'Diagnosed with Depression',
  ever_diagnosed_anxiety:        'Diagnosed with Anxiety',
  ever_diagnosed_dep_or_anx:     'Diagnosed with Depression or Anxiety',
};

const CONDITION_LABEL: Record<PriorCondition, string> = {
  diagnosed:     'teens who have been diagnosed with depression or anxiety',
  not_diagnosed: 'teens who have not been diagnosed with depression or anxiety',
};

@Component({
  selector: 'app-elicitation-modal',
  templateUrl: './modal.html',
  styleUrls: ['./modal.css']
})
export class ElicitationModalComponent {
  @Input() datasetId: string = '';
  @Input() attributes: string[] = [];
  @Input() columnValues: Record<string, number[]> = {};
  @Input() categoricalValues: Record<string, string[]> = {};

  @Output() closed = new EventEmitter<void>();

  selectedAttribute: string | null = null;
  currentCondition: PriorCondition = 'diagnosed';
  draftCounts: number[] = [];
  confidenceStep = false;
  confidenceRating: number = 50;
  private pendingBelief: PriorBelief | null = null;
  private draftsByCondition: Partial<Record<PriorCondition, number[]>> = {};

  constructor(
    private store: PriorBeliefStore,
    public binningService: BinningService,
    private chatService: ChatService,
    private global: SessionPage
  ) {}

  get allAttributes(): string[] {
    return this.attributes.concat(Object.keys(this.categoricalValues));
  }

  get conditionLabel(): string {
    return CONDITION_LABEL[this.currentCondition];
  }

  cleanAttr(attr: string): string {
    return ATTR_LABELS[attr] ?? attr.replace(/_/g, ' ');
  }

  get conditionStep(): number {
    return this.currentCondition === 'diagnosed' ? 1 : 2;
  }

  isCategorical(attr: string): boolean {
    return attr in this.categoricalValues;
  }

  get currentValues(): number[] {
    if (!this.selectedAttribute || this.isCategorical(this.selectedAttribute)) return [];
    return this.columnValues[this.selectedAttribute] || [];
  }

  get currentFixedBins(): Bin[] {
    if (!this.selectedAttribute || !this.isCategorical(this.selectedAttribute)) return [];
    return this.binningService.categoricalBins(this.categoricalValues[this.selectedAttribute]);
  }

  get remaining(): number {
    return this.binningService.ballCount - this.draftCounts.reduce((a, b) => a + b, 0);
  }

  selectAttribute(attr: string) {
    this.selectedAttribute = attr;
    this.currentCondition = 'diagnosed';
    this.confidenceStep = false;
    this.draftsByCondition = {};
    this.confidenceRating = this.store.getBelief(this.datasetId, attr, 'diagnosed')?.confidence ?? 50;
    this._loadDraft();
  }

  private _loadDraft() {
    if (!this.selectedAttribute) return;
    // priority: in-session draft > saved store belief > empty
    if (this.draftsByCondition[this.currentCondition]) {
      this.draftCounts = this.draftsByCondition[this.currentCondition]!.slice();
    } else {
      const existing = this.store.getBelief(this.datasetId, this.selectedAttribute, this.currentCondition);
      if (existing) {
        this.draftCounts = existing.counts.slice();
        this.draftsByCondition[this.currentCondition] = existing.counts.slice();
      } else if (this.isCategorical(this.selectedAttribute)) {
        this.draftCounts = this.binningService.emptyCountsFor(this.categoricalValues[this.selectedAttribute].length);
      } else {
        const bins = this.binningService.computeBins(this.currentValues);
        this.draftCounts = this.binningService.emptyCountsFor(bins.length);
      }
    }
  }

  counterClass: 'pop-a' | 'pop-b' = 'pop-a';

  onCountsChange(counts: number[]) {
    this.draftCounts = counts;
    this.draftsByCondition[this.currentCondition] = counts.slice();
    this.counterClass = this.counterClass === 'pop-a' ? 'pop-b' : 'pop-a';
  }

  hasPrior(attr: string): boolean {
    return this.store.hasBothBeliefs(this.datasetId, attr);
  }

  hasConditionPrior(attr: string, condition: PriorCondition): boolean {
    return !!this.store.getBelief(this.datasetId, attr, condition);
  }

  reset() {
    if (this.selectedAttribute && this.isCategorical(this.selectedAttribute)) {
      this.draftCounts = this.binningService.emptyCountsFor(this.categoricalValues[this.selectedAttribute].length);
    } else {
      this.draftCounts = this.binningService.emptyBallCounts();
    }
  }

  save() {
    if (!this.selectedAttribute) return;
    let belief: PriorBelief;
    if (this.isCategorical(this.selectedAttribute)) {
      const cats = this.categoricalValues[this.selectedAttribute];
      belief = {
        datasetId: this.datasetId,
        attribute: this.selectedAttribute,
        condition: this.currentCondition,
        binEdges: cats.map((_, i) => i).concat(cats.length),
        counts: this.draftCounts,
        ballCount: this.binningService.ballCount,
        columnHash: '',
        createdAt: Date.now(),
        categories: cats
      };
    } else {
      const bins = this.binningService.computeBins(this.currentValues);
      belief = {
        datasetId: this.datasetId,
        attribute: this.selectedAttribute,
        condition: this.currentCondition,
        binEdges: bins.map(b => b.lo).concat(bins[bins.length - 1].hi),
        counts: this.draftCounts,
        ballCount: this.binningService.ballCount,
        columnHash: '',
        createdAt: Date.now()
      };
    }
    this.pendingBelief = belief;
    this.confidenceStep = true;
    const saved = this.store.getBelief(this.datasetId, this.selectedAttribute, this.currentCondition);
    this.confidenceRating = saved?.confidence ?? 50;
  }

  submitConfidence() {
    if (!this.pendingBelief) return;
    const belief = { ...this.pendingBelief, confidence: Number(this.confidenceRating) };
    console.log('[prior] submitConfidence sending:', belief.attribute, belief.condition, 'confidence=', belief.confidence);
    this.store.setBelief(belief);
    this.chatService.sendPriors({
      participantId: this.global.participantId,
      participantIdSource: this.global.participantIdSource,
      appMode: this.global.appMode,
      appType: this.global.appType,
      appLevel: this.global.appLevel,
      priors: belief,
    });
    this.pendingBelief = null;
    this.confidenceStep = false;
    this.confidenceRating = 50;

    if (this.currentCondition === 'diagnosed') {
      // move to second condition, preserving any existing not_diagnosed draft
      this.currentCondition = 'not_diagnosed';
      this.confidenceRating = this.store.getBelief(this.datasetId, this.selectedAttribute ?? '', 'not_diagnosed')?.confidence ?? 50;
      this._loadDraft();
    } else {
      // both conditions done — back to attribute picker
      this.selectedAttribute = null;
      this.draftCounts = [];
    }
  }

  goBack() {
    // always persist current draft before navigating away
    this.draftsByCondition[this.currentCondition] = this.draftCounts.slice();

    if (this.confidenceStep) {
      // confidence → distribution (same condition)
      this.confidenceStep = false;
      this.pendingBelief = null;
      this._loadDraft();
    } else if (this.currentCondition === 'not_diagnosed') {
      // not_diagnosed distribution → diagnosed confidence
      this.currentCondition = 'diagnosed';
      this.confidenceStep = true;
      const existing = this.selectedAttribute ? this.store.getBelief(this.datasetId, this.selectedAttribute, 'diagnosed') : null;
      if (existing) {
        this.pendingBelief = { ...existing };
        this.confidenceRating = existing.confidence ?? 50;
      }
    } else {
      // diagnosed distribution → picker
      this.selectedAttribute = null;
    }
  }

  get canClose(): boolean {
    return this.allAttributes.every(attr => this.hasPrior(attr));
  }

  close() {
    if (!this.canClose) return;
    this.closed.emit();
  }
}
