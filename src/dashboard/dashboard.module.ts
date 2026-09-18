import { Module } from '@nestjs/common';
import { DashboardLifecycleModule } from './dashboard-lifecycle.module';
import { DashboardController } from './dashboard.controller';
import { DashboardService } from './dashboard.service';
import { MongooseModule } from '@nestjs/mongoose';
import { Dashboard, DashboardSchema } from './dashboard.schema';
import { Section, SectionSchema } from 'src/sections/sections.schema';

@Module({
  imports: [
    MongooseModule.forFeature([
      { name: Dashboard.name, schema: DashboardSchema },
      { name: Section.name, schema: SectionSchema }
    ]),
    DashboardLifecycleModule,
  ],
  controllers: [DashboardController],
  providers: [DashboardService]
})
export class DashboardModule { }
