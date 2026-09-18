import { Module } from '@nestjs/common';
import { DashboardLifecycleService } from './dashboard-lifecycle.service';

@Module({ providers: [DashboardLifecycleService], exports: [DashboardLifecycleService] })
export class DashboardLifecycleModule {}
