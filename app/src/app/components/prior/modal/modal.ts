import { Component, Input, Output, EventEmitter } from '@angular/core';
import { Bin, PriorBelief } from '../../../models/prior-belief';
import { PriorBeliefStore } from '../../../store/prior-belief.store';
import { BinningService } from '../../../services/binning.service';
import { ChatService } from '../../../services/socket.service';
import { SessionPage } from '../../../models/config';

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
  draftCounts: number[] = [];

  constructor(
    private store: PriorBeliefStore,
    public binningService: BinningService,
    private chatService: ChatService,
    private global: SessionPage
  ) {}

  get allAttributes(): string[] {
    return this.attributes.concat(Object.keys(this.categoricalValues));
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
    const existing = this.store.getBelief(this.datasetId, attr);
    if (existing) {
      this.draftCounts = existing.counts.slice();
    } else if (this.isCategorical(attr)) {
      this.draftCounts = this.binningService.emptyCountsFor(this.categoricalValues[attr].length);
    } else {
      this.draftCounts = this.binningService.emptyBallCounts();
    }
  }

  counterClass: 'pop-a' | 'pop-b' = 'pop-a';

  onCountsChange(counts: number[]) {
    this.draftCounts = counts;
    this.counterClass = this.counterClass === 'pop-a' ? 'pop-b' : 'pop-a';
  }

  hasPrior(attr: string): boolean {
    return !!this.store.getBelief(this.datasetId, attr);
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
        binEdges: bins.map(b => b.lo).concat(bins[bins.length - 1].hi),
        counts: this.draftCounts,
        ballCount: this.binningService.ballCount,
        columnHash: '',
        createdAt: Date.now()
      };
    }
    this.store.setBelief(belief);
    // Incrementally transport the just-saved prior to the server (Option A) so
    // it's available to the JS confirmation-bias metric. Keyed by attribute
    // server-side, last-write-wins.
    this.chatService.sendPriors({
      participantId: this.global.participantId,
      appMode: this.global.appMode,
      appType: this.global.appType,
      appLevel: this.global.appLevel,
      priors: belief,
    });
    this.selectedAttribute = null;
    this.draftCounts = [];
  }

  close() {
    this.closed.emit();
  }
}
