import { BadRequestException, Injectable, NotFoundException } from '@nestjs/common';
import { InjectConnection } from '@nestjs/mongoose';
import { Connection, Types } from 'mongoose';

interface LifecycleDocument {
  _id: Types.ObjectId;
  sections?: Types.ObjectId[];
  access?: { dashboard: Types.ObjectId; sections: Types.ObjectId[] }[];
  generatedWorkspaceId?: string;
  workspaceId?: string;
  deletedAt?: Date;
  show?: boolean;
  menu_initialized?: boolean;
}

@Injectable()
export class DashboardLifecycleService {
  constructor(@InjectConnection() private readonly connection: Connection) {}

  private collection(name: string) { return this.connection.db.collection<LifecycleDocument>(name); }
  private workspaces() { return this.connection.db.collection<{ _id: string; deletedAt?: Date; menu_initialized?: boolean }>('analytics_workspaces'); }
  private objectId(id: string) {
    if (!Types.ObjectId.isValid(id)) throw new BadRequestException('Identificador inválido.');
    return new Types.ObjectId(id);
  }

  private async retireWorkspaceIfUnused(workspaceId: string, when: Date) {
    const sections = await this.collection('sections').find({ workspaceId, deletedAt: null }).toArray();
    const ids = sections.map(s => s._id);
    const usedElsewhere = ids.length && await this.collection('dashboards').findOne({ deletedAt: null, sections: { $in: ids } });
    if (usedElsewhere) return;
    // Keep the source file and the saved configuration; a tombstone prevents recovery into the menu.
    await this.workspaces().updateOne({ _id: workspaceId }, { $set: { deletedAt: when, menu_initialized: true } });
    await this.collection('sections').updateMany({ workspaceId, deletedAt: null }, { $set: { deletedAt: when, show: false } });
    if (ids.length) {
      await this.collection('dashboards').updateMany({}, { $pullAll: { sections: ids } });
      await this.collection('users').updateMany({ 'access.sections': { $in: ids } }, { $pull: { 'access.$[].sections': { $in: ids } } });
    }
  }

  async deleteDashboard(id: string) {
    const dashboard = await this.collection('dashboards').findOne({ _id: this.objectId(id) });
    if (!dashboard) throw new NotFoundException('El tablero no existe.');
    const sections = await this.collection('sections').find({ _id: { $in: dashboard.sections || [] } }).toArray();
    const workspaces = new Set(sections.map(s => s.workspaceId).filter(Boolean));
    if (dashboard.generatedWorkspaceId) workspaces.add(dashboard.generatedWorkspaceId);
    const when = dashboard.deletedAt || new Date();
    // Hide first, then clean references. Retrying also completes an interrupted deletion.
    await this.collection('dashboards').updateOne({ _id: dashboard._id }, { $set: { deletedAt: when, show: false } });
    await this.collection('users').updateMany({}, { $pull: { access: { dashboard: dashboard._id } } });
    for (const workspaceId of workspaces) await this.retireWorkspaceIfUnused(workspaceId, when);
    return { message: 'Tablero eliminado.' };
  }

  async deleteSection(id: string) {
    const sectionId = this.objectId(id);
    const section = await this.collection('sections').findOne({ _id: sectionId });
    if (!section) throw new NotFoundException('La sección no existe.');
    const when = section.deletedAt || new Date();
    await this.collection('sections').updateOne({ _id: sectionId }, { $set: { deletedAt: when, show: false } });
    await this.collection('users').updateMany({ 'access.sections': sectionId }, { $pull: { 'access.$[].sections': sectionId } });
    // Reconcile before pulling the reference, so a failed operation remains discoverable and retryable.
    const removed = await this.reconcileGenerated();
    await this.collection('dashboards').updateMany({}, { $pull: { sections: sectionId } });
    if (section.workspaceId) await this.retireWorkspaceIfUnused(section.workspaceId, when);
    return { ...section, deletedAt: when, removedDashboards: removed.removed };
  }

  async reconcileGenerated(onlyIds?: string[]) {
    const filter: any = { generatedWorkspaceId: { $exists: true }, deletedAt: null };
    if (onlyIds) filter._id = { $in: onlyIds.map(id => this.objectId(id)) };
    const dashboards = await this.collection('dashboards').find(filter).toArray();
    const removed: string[] = [];
    for (const dashboard of dashboards) {
      // Empty containers and hidden sections are intentional. Only remove dangling/deleted references.
      if (!dashboard.sections?.length) continue;
      const liveSection = await this.collection('sections').findOne({ _id: { $in: dashboard.sections }, deletedAt: null });
      if (liveSection) continue;
      await this.deleteDashboard(dashboard._id.toString());
      removed.push(dashboard._id.toString());
    }
    return { removed };
  }
}
